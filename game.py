from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from database import db, Character, normalize_name, BattleLog, Item, User, CharacterItem, MiningLottery, LotteryEntry, LotteryWinner, Message, MessageReport, Jail, NPC, Quest, QuestObjective, QuestReward, PlayerQuest, QuestProgress # Updated imports
from flask import g
from auth import load_translations, get_current_language
import json
import os
import random
from datetime import datetime, timedelta
from cache_helpers import get_cached_rankings
from functools import wraps
from markupsafe import Markup
from sqlalchemy import or_ 

game_bp = Blueprint('game', __name__)

def not_jailed(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated:
            if request.endpoint == 'game.create_character':
                return f(*args, **kwargs)
            
            if not hasattr(current_user, 'character') or current_user.character is None:
                return redirect(url_for('game.create_character'))
            
            if current_user.character.is_jailed:
                jail_record = current_user.character.current_jail
                if datetime.utcnow() > jail_record.start_time + timedelta(minutes=jail_record.duration):
                    current_user.character.is_jailed = False
                    jail_record.is_released = True
                    db.session.commit()
                else:
                    flash("You are currently jailed and cannot perform this action", 'error')
                    return redirect(url_for('game.jail'))
        return f(*args, **kwargs)
    return decorated_function

def load_faction_stats():
    config_path = os.path.join(current_app.root_path, 'config', 'factions.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)['factions']
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        current_app.logger.error(f"Failed to load faction stats: {str(e)}")
        return {}

def get_faction_resource_name(faction_key):
    faction_stats = load_faction_stats()
    return faction_stats.get(faction_key, {}).get('stats', {}).get('resource_name', 'Resource')

def get_faction_resource_image(faction_key):
    faction_stats = load_faction_stats()
    return faction_stats.get(faction_key, {}).get('stats', {}).get('resource_image', 'resource.webp')

# Add this new function to game.py
def get_faction_resource_info(faction_key):
    return {
        'name': get_faction_resource_name(faction_key),
        'image': get_faction_resource_image(faction_key)
    }

@game_bp.before_request
def update_last_activity():
    if current_user.is_authenticated:
        current_user.last_activity = datetime.utcnow()
        db.session.commit()


def calculate_player_variable(character):
    if character.faction.lower() == 'veylan':
        main_stat = character.destreza
    elif character.faction.lower() == 'urghan':
        main_stat = character.forca
    elif character.faction.lower() == 'aureen':
        main_stat = character.inteligencia
    elif character.faction.lower() == 'camyra':
        main_stat = character.devocao
    else:
        main_stat = 0  # fallback
    
    other_stats = (character.forca + character.destreza + 
                  character.inteligencia + character.devocao - main_stat)
    
    return (0.75 * main_stat) + (0.35 * other_stats)

def calculate_weapon_damage(character):
    weapon_damage = 0
    weapon_name = "bare hands"
    
    if isinstance(character, NPC):
        if character.weapon and 'damage' in character.weapon.stats:
            dice_count, dice_type = map(int, character.weapon.stats['damage'].split('d'))
            weapon_damage = sum(random.randint(1, dice_type) for _ in range(dice_count))
            weapon_name = character.weapon.translation_key.replace('_', ' ').title()
        return weapon_damage, weapon_name
    
    for item in character.items:
        if item.equipped and item.item.item_type == 'weapon' and 'damage' in item.item.stats:
            dice_count, dice_type = map(int, item.item.stats['damage'].split('d'))
            weapon_damage = sum(random.randint(1, dice_type) for _ in range(dice_count))
            weapon_name = item.item.translation_key.replace('_', ' ').title()
            break
    
    return weapon_damage, weapon_name

def calculate_damage(attacker, defender):
    attacker_var = calculate_player_variable(attacker)
    defender_var = calculate_player_variable(defender)
    stat_difference = max(0, attacker_var - defender_var)
    weapon_damage, weapon_name = calculate_weapon_damage(attacker)
    base_damage = random.randint(1, 3)
    total_damage = int(stat_difference + weapon_damage + base_damage)
    return total_damage, weapon_name

@game_bp.route('/create-character', methods=['GET', 'POST'])
@login_required
def create_character():
    ip_warning = False
    if current_user.ip_address:
        ip_count = User.query.filter_by(ip_address=current_user.ip_address).count()
        if ip_count > 1:
            ip_warning = True
            
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    g.pop('user_xp_rank', None)
    g.pop('user_level_rank', None)
    g.pop('user_kills_rank', None)
    g.pop('user_deaths_rank', None)
    g.pop('recent_players', None)
    
    if hasattr(current_user, 'character') and current_user.character:
        flash(g.translations['character_creation']['already_exists'])
        return redirect(url_for('game.dashboard'))
    
    if current_user.character:
        flash(g.translations['game']['character_creation']['already_exists'])
        return redirect(url_for('game.dashboard'))
    
    faction_stats = load_faction_stats()
    
    if request.method == 'POST':
        name = request.form.get('character_name').strip()
        faction = request.form.get('faction')
        
        if not name or not faction:
            flash(g.translations['game']['character_creation']['required_fields'])
            return redirect(url_for('game.create_character'))
            
        existing_character = Character.query.filter_by(name=name).first()
        if existing_character:
            flash(g.translations['character_creation']['name_exists'])
            return redirect(url_for('game.create_character'))
            
        try:
            stats = faction_stats.get(faction, {}).get('stats', {})
            
            character = Character(
                user_id=current_user.id,
                name=name,
                normalized_name=normalize_name(name),
                faction=faction,
                level=1,
                current_xp=0,
                xp_to_next_level=150,
                destreza=stats.get('destreza', {}).get('base', 10),
                forca=stats.get('forca', {}).get('base', 10),
                inteligencia=stats.get('inteligencia', {}).get('base', 10),
                devocao=stats.get('devocao', {}).get('base', 10),
                destreza_per_level=stats.get('destreza', {}).get('per_level', 1.0),
                forca_per_level=stats.get('forca', {}).get('per_level', 1.0),
                inteligencia_per_level=stats.get('inteligencia', {}).get('per_level', 1.0),
                devocao_per_level=stats.get('devocao', {}).get('per_level', 1.0),
                healthpoints_per_level=stats.get('healthpoints', {}).get('per_level', 10)
            )
            
            db.session.add(character)
            db.session.flush()
            
            welcome_message = Message(
                sender_id=1,
                recipient_id=character.id,
                subject="Welcome to the Game!",
                body="Welcome to our game! We're excited to have you here.\n\n" +
                     "Here are some tips to get started:\n" +
                     "1. Visit the Academy to train your attributes\n" +
                     "2. Check the shop for equipment\n" +
                     "3. Explore the world and battle other players\n\n" +
                     "Good luck on your adventures!",
                is_admin_message=True
                )
        
            db.session.add(welcome_message)
            db.session.commit()
            
            return redirect(url_for('game.dashboard'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error("Character creation failed", exc_info=True)
            flash(g.translations['errors']['account_creation_failed'])
            return redirect(url_for('game.create_character'))
    
    factions = []
    if 'game' in g.translations and 'factions' in g.translations['game']:
        for faction_key, faction_data in g.translations['game']['factions'].items():
            faction_info = {
                'key': faction_key,
                'name': faction_data['name'],
                'color': faction_data['color'],
                'description': faction_data.get('description', ''),
                'motto': faction_data.get('motto', ''),
                'belief': faction_data.get('belief', ''),
                'valor': faction_data.get('valor', ''),
                'stat_values': faction_stats.get(faction_key, {}).get('stats', {}),
                'skill_name': faction_data.get('skill_name', '')
            }
            factions.append(faction_info)
    
    return render_template('create_character.html',
                         translations=g.translations,
                         factions=factions,
                         ip_warning=ip_warning)

def apply_quest_penalty(player_quest):
    character = player_quest.character
    try:
        character.reputation -= 2
        flash("Penalty: Lost 2 reputation for failing the quest", 'warning')
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error applying quest penalty: {str(e)}")

def award_quest_rewards(player_quest):
    character = player_quest.character
    quest = player_quest.quest
    
    try:
        character.reputation += 2
        flash("Reward: 2 reputation", 'success')
        for reward in quest.rewards:
            if reward.reward_type == 'gold':
                character.gold += reward.amount
                flash(f"Reward: {reward.amount} gold", 'success')
            elif reward.reward_type == 'diamonds':
                character.diamonds += reward.amount
                flash(f"Reward: {reward.amount} diamonds", 'success')
            elif reward.reward_type == 'xp':
                character.add_xp(reward.amount)
                flash(f"Reward: {reward.amount} XP", 'success')
            elif reward.reward_type == 'reputation':
                character.reputation += reward.amount
                flash(f"Reward: {reward.amount} reputation", 'success')
            elif reward.reward_type == 'item' and reward.item:
                existing_item = CharacterItem.query.filter_by(
                    character_id=character.id,
                    item_id=reward.item.id
                ).first()
                
                if not existing_item:
                    character_item = CharacterItem(
                        character_id=character.id, 
                        item_id=reward.item.id,
                        equipped=False
                    )
                    db.session.add(character_item)
                    item_name = g.translations['game']['items'][reward.item.item_type][reward.item.translation_key]['name']
                    flash(f"Reward: {item_name}", 'success')
                else:
                    item_name = g.translations['game']['items'][reward.item.item_type][reward.item.translation_key]['name']
                    flash(f"Reward: {item_name} (already in inventory)", 'info')
            elif reward.reward_type == 'lottery_tickets':
                character.lottery_tickets += reward.amount
                flash(f"Reward: {reward.amount} lottery tickets", 'success')
            elif reward.reward_type == 'attribute':
                attribute_name = reward.attribute_type
                attribute_amount = reward.attribute_amount
                if hasattr(character, attribute_name):
                    current_value = getattr(character, attribute_name)
                    setattr(character, attribute_name, current_value + attribute_amount)
                    flash(f"Reward: Gained {attribute_amount} {attribute_name}", 'success')
                else:
                    current_app.logger.warning(f"Attempted to award unknown attribute: {attribute_name}")
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error awarding quest rewards: {str(e)}")
        flash("An error occurred while awarding rewards", 'error')

def check_and_update_quests(character, objective_type, amount=1, target_value=None, attribute_trained=None, attribute_amount=0):
    active_quests = PlayerQuest.query.filter_by(
        character_id=character.id, 
        is_completed=False, 
        is_failed=False
    ).all()

    for active_quest in active_quests:
        for objective in active_quest.quest.objectives:
            if objective.objective_type == objective_type:
                if objective_type == 'train_attribute' and objective.target_value != attribute_trained:
                    continue
                
                if objective_type in ['deposit_gold', 'withdraw_gold']:
                    progress = QuestProgress.query.filter_by(
                        player_quest_id=active_quest.id,
                        objective_id=objective.id
                    ).first()

                    if not progress:
                        progress = QuestProgress(
                            character_id=character.id,
                            player_quest_id=active_quest.id,
                            objective_id=objective.id,
                            progress_value=0
                        )
                        db.session.add(progress)

                    progress.progress_value += amount
                else:
                    progress = QuestProgress.query.filter_by(
                        player_quest_id=active_quest.id,
                        objective_id=objective.id
                    ).first()

                    if not progress:
                        progress = QuestProgress(
                            character_id=character.id,
                            player_quest_id=active_quest.id,
                            objective_id=objective.id,
                            progress_value=0
                        )
                        db.session.add(progress)

                    if objective_type == 'train_attribute':
                        progress.progress_value += attribute_amount
                    else:
                        progress.progress_value += 1

                if progress.progress_value >= objective.amount_required:
                    all_completed = True
                    for obj in active_quest.quest.objectives:
                        obj_progress = QuestProgress.query.filter_by(
                            player_quest_id=active_quest.id,
                            objective_id=obj.id
                        ).first()
                        
                        if not obj_progress or obj_progress.progress_value < obj.amount_required:
                            all_completed = False
                            break

                    if all_completed:
                        active_quest.is_completed = True
                        active_quest.completed_at = datetime.utcnow()
                        award_quest_rewards(active_quest)
                
                db.session.commit()
                break


@game_bp.context_processor
def utility_processor():
    def get_objective_text(objective):
        try:
            translations = g.translations['game']['quests']
            target = objective.target_value or ''

            if objective.objective_type == 'train_attribute' and target:
                attribute_name = translations.get('attributes', {}).get(target, target)
            # Removed redundant check for attribute_name
                return translations['train_attribute'].format(
                    amount=objective.amount_required,
                    attribute=attribute_name
                )

            if objective.objective_type == 'buy_specific_item' and target:
                item_name = ""
                for item_type_key in ['weapon', 'armor', 'magic']: 
                    if target in g.translations['game']['items'].get(item_type_key, {}):
                        item_name = g.translations['game']['items'][item_type_key][target]['name']
                        break
                target = item_name if item_name else target

            return {
                'kill_other_faction': translations['kill_other_faction'].format(amount=objective.amount_required),
                'kill_enemy_faction': translations['kill_enemy_faction'].format(amount=objective.amount_required),
                'kill_npc': translations['kill_npc'].format(amount=objective.amount_required, npc=target),
                'mine_resources': translations['mine_resources'].format(amount=objective.amount_required),
                'enter_lottery': translations['enter_lottery'],
                'deposit_gold': translations['deposit_gold'].format(amount=objective.amount_required),
                'withdraw_gold': translations['withdraw_gold'].format(amount=objective.amount_required),
                'buy_from_store': translations['buy_from_store'].format(store=target),
                'buy_specific_item': translations['buy_specific_item'].format(item=target),
                # Removed the 'train_attribute' entry from here
            }.get(objective.objective_type, f"{objective.objective_type}: {objective.amount_required}")
        except Exception as e:
            current_app.logger.error(f"Error getting objective text: {str(e)}")
            return f"{objective.objective_type}: {objective.amount_required}"
    
    def get_reward_text(reward):
        try:
            translations = g.translations['game']['quests']
            if not reward:
                return "Unknown reward"

            if reward.reward_type == 'gold':
                return translations['gold'].format(amount=reward.amount)
            elif reward.reward_type == 'diamonds':
                return translations['diamonds'].format(amount=reward.amount)
            elif reward.reward_type == 'xp':
                return translations['xp'].format(amount=reward.amount)
            elif reward.reward_type == 'reputation':
                return translations['reputation'].format(amount=reward.amount)
            elif reward.reward_type == 'item' and reward.item:
                item_text = ""
                item_type_key = reward.item.item_type 
                translation_key = reward.item.translation_key
                if item_type_key in g.translations['game']['items'] and translation_key in g.translations['game']['items'][item_type_key]:
                    item_text = g.translations['game']['items'][item_type_key][translation_key]['name']
                else:
                    item_text = translation_key 
                return f"{translations['item']}: {item_text}"
            elif reward.reward_type == 'lottery_tickets':
                return translations['lottery_tickets'].format(amount=reward.amount)
            elif reward.reward_type == 'attribute':
                attribute_name = translations.get('attributes', {}).get(reward.attribute_type, reward.attribute_type)
            # Removed redundant check for attribute_name, as .get() handles defaults
                return translations['attribute'].format( # Use 'attribute' here
                    amount=reward.amount,
                    attribute=attribute_name
                )
            else:
                return f"{reward.reward_type}: {reward.amount}"
        except Exception as e:
            current_app.logger.error(f"Error getting reward text: {str(e)}")
            return f"{reward.reward_type}: {reward.amount}"

    def get_faction_resource_info(faction_key):
        faction_stats = load_faction_stats()
        faction_data = faction_stats.get(faction_key, {})
        return {
            'name': faction_data.get('stats', {}).get('resource_name', 'Resource'),
            'image': faction_data.get('stats', {}).get('resource_image', 'resource.webp')
        }
            
    return dict(get_objective_text=get_objective_text, get_reward_text=get_reward_text, get_faction_resource_info=get_faction_resource_info)


def assign_daily_quests(character):
    active_quests = Quest.query.filter_by(is_active=True).all()
    
    completed_unique_quests = [pq.quest_id for pq in character.quests 
                             if pq.is_completed and pq.quest.is_unique]
    available_quests = [q for q in active_quests 
                       if not q.is_unique or q.id not in completed_unique_quests]
    
    # Select 4 random quests weighted by spawn_chance
    if available_quests:
        weights = [q.spawn_chance for q in available_quests]
        selected_quests = random.choices(available_quests, weights=weights, k=min(4, len(available_quests)))
        
        for quest in selected_quests:
            existing = PlayerQuest.query.filter_by(
                character_id=character.id,
                quest_id=quest.id,
                started_at=None
            ).first()
            
            if not existing:
                pq = PlayerQuest(
                    character_id=character.id,
                    quest_id=quest.id
                )
                db.session.add(pq)
        
        db.session.commit()

def get_available_quests(character):
    return PlayerQuest.query.filter_by(
        character_id=character.id,
        is_completed=False,
        is_failed=False
    ).all()

def track_quest_progress(character, objective_type, amount=1, target_value=None):
    active_quest = PlayerQuest.query.filter(
        PlayerQuest.character_id == character.id,
        PlayerQuest.is_completed == False,
        PlayerQuest.is_failed == False,
        PlayerQuest.started_at != None
    ).first()
    
    if not active_quest:
        return
    
    quest_objectives = active_quest.quest.objectives
    for objective in quest_objectives:
        if objective.objective_type == objective_type:
            if objective.target_value and objective.target_value != str(target_value):
                continue
            
            progress = QuestProgress.query.filter_by(
                character_id=character.id,
                objective_id=objective.id
            ).first()
            
            if not progress:
                progress = QuestProgress(
                    character_id=character.id,
                    objective_id=objective.id,
                    progress_value=0
                )
                db.session.add(progress)
            
            progress.progress_value += amount
            progress.last_updated = datetime.utcnow()
            
            if progress.progress_value >= objective.amount_required:
                complete_quest(active_quest)
            
            db.session.commit()
            break

def complete_quest(player_quest):
    player_quest.is_completed = True
    player_quest.completed_at = datetime.utcnow()
    
    character = player_quest.character
    
    
    for reward in player_quest.quest.rewards:
        if reward.reward_type == 'gold':
            character.gold += reward.amount
        elif reward.reward_type == 'diamonds':
            character.diamonds += reward.amount
        elif reward.reward_type == 'xp':
            character.add_xp(reward.amount)
        elif reward.reward_type == 'item' and reward.item:
            ci = CharacterItem(
                character_id=character.id,
                item_id=reward.item.id,
                equipped=False
            )
            db.session.add(ci)
    
    db.session.commit()


@game_bp.route('/quests', methods=['GET', 'POST'])
@login_required
@not_jailed
def quests():
    character = current_user.character
    
    
    current_time = datetime.utcnow()
    quest_refresh_interval_hours = 21
    
    active_quest = PlayerQuest.query.filter_by(
        character_id=character.id,
        is_completed=False,
        is_failed=False
    ).first()

    completed_quests = PlayerQuest.query.filter_by(
        character_id=character.id
    ).filter(
        or_(PlayerQuest.is_completed == True, PlayerQuest.is_failed == True)
    ).order_by(
        PlayerQuest.completed_at.desc()
    ).all()

    can_accept_new_quest = True
    time_until_new_quest = timedelta(seconds=0)

    if active_quest:
        can_accept_new_quest = False
        time_elapsed = current_time - active_quest.started_at
        time_remaining = timedelta(hours=quest_refresh_interval_hours) - time_elapsed
        
        if time_remaining.total_seconds() > 0:
            time_until_new_quest = time_remaining
        else:
            if not active_quest.is_completed and not active_quest.is_failed:
                active_quest.is_failed = True
                active_quest.completed_at = current_time
                apply_quest_penalty(active_quest)
                db.session.commit()
                flash(g.translations['game']['quests']['quest_failed_timed_out'].format(title=active_quest.quest.title), 'warning')
                active_quest = None 
                can_accept_new_quest = True 
            else:
                can_accept_new_quest = False 

    available_quests = []
    if can_accept_new_quest:
        excluded_quest_ids = [pq.quest_id for pq in completed_quests]
        
        all_available_quests = Quest.query.filter(
            Quest.is_active == True,
            Quest.id.notin_(excluded_quest_ids) 
        ).all()
        
        # Select 4 random quests
        if len(all_available_quests) > 4:
            available_quests = random.sample(all_available_quests, 4)
        else:
            available_quests = all_available_quests
        
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'accept_quest':
            if not can_accept_new_quest:
                flash(g.translations['game']['quests']['cannot_accept_yet'], 'error')
                return redirect(url_for('game.quests'))
            
            quest_id = request.form.get('quest_id', type=int)
            quest_to_accept = Quest.query.get(quest_id)

            if not quest_to_accept or not quest_to_accept.is_active:
                flash(g.translations['game']['quests']['invalid_quest'], 'error')
                return redirect(url_for('game.quests'))
            
            faction_resource_info = get_faction_resource_info(character.faction)
            resource_name = faction_resource_info['name']
            if character.resource < 1:
                flash(g.translations['game']['quests']['not_enough_resource'].format(resource_name=resource_name), 'error')
                return redirect(url_for('game.quests'))
            
            character.resource -= 1
            
            player_quest = PlayerQuest(
                character_id=character.id,
                quest_id=quest_to_accept.id,
                started_at=current_time,
                last_quest_refresh=current_time 
            )
            db.session.add(player_quest)
            db.session.flush() 

            for objective in quest_to_accept.objectives:
                quest_progress = QuestProgress(
                    character_id=character.id,
                    player_quest_id=player_quest.id,
                    objective_id=objective.id,
                    progress_value=0
                )
                db.session.add(quest_progress)
            
            db.session.commit()
            flash(g.translations['game']['quests']['quest_accepted'].format(title=quest_to_accept.title), 'success')
            return redirect(url_for('game.quests'))

        elif action == 'abandon_quest':
            if not active_quest:
                flash(g.translations['game']['quests']['no_active_quest_to_abandon'], 'error')
                return redirect(url_for('game.quests'))
            
            active_quest.is_failed = True
            active_quest.completed_at = current_time
            character.reputation -= 2
            db.session.commit()
            flash(g.translations['game']['quests']['quest_abandoned'].format(title=active_quest.quest.title), 'warning')
            return redirect(url_for('game.quests'))


    return render_template('quests.html',
                           translations=g.translations,
                           active_quest=active_quest,
                           available_quests=available_quests,
                           completed_quests=completed_quests,
                           can_accept_new_quest=can_accept_new_quest,
                           time_until_new_quest=time_until_new_quest)

@game_bp.route('/quests/accept/<int:quest_id>', methods=['POST'])
@login_required
@not_jailed
def accept_quest(quest_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    
    active_quest = PlayerQuest.query.filter(
        PlayerQuest.character_id == character.id,
        PlayerQuest.is_completed == False,
        PlayerQuest.is_failed == False,
        PlayerQuest.started_at != None
    ).first()
    
    if active_quest:
        flash("You already have an active quest!", 'error')
        return redirect(url_for('game.quests'))
    
    quest = Quest.query.filter_by(id=quest_id, is_active=True).first()
    if not quest:
        flash("Quest not available!", 'error')
        return redirect(url_for('game.quests'))
    
    if quest.is_unique:
        completed = PlayerQuest.query.filter_by(
            character_id=character.id,
            quest_id=quest.id,
            is_completed=True
        ).first()
        if completed:
            flash("You've already completed this unique quest!", 'error')
            return redirect(url_for('game.quests'))
    
    if character.resource < 1:
        flash("Not enough resources to accept quest!", 'error')
        return redirect(url_for('game.quests'))
    
    character.resource -= 1
    
    player_quest = PlayerQuest(
        character_id=character.id,
        quest_id=quest.id,
        started_at=datetime.utcnow()
    )
    
    db.session.add(player_quest)
    db.session.commit()
    
    flash("Quest accepted!", 'success')
    return redirect(url_for('game.quests'))

@game_bp.route('/quests/abandon/<int:quest_id>', methods=['POST'])
@login_required
@not_jailed
def abandon_quest(quest_id):
    player_quest = PlayerQuest.query.filter_by(
        character_id=current_user.character.id,
        quest_id=quest_id,
        is_completed=False,
        is_failed=False
    ).first_or_404()
    
    apply_quest_penalty(player_quest)
    player_quest.is_failed = True
    player_quest.completed_at = datetime.utcnow()
    db.session.commit()
    
    flash(g.translations['game']['quests']['quest_abandoned'].format(title=player_quest.quest.title), 'warning')
    return redirect(url_for('game.quests'))

@game_bp.route('/player/<int:character_id>')
@login_required
@not_jailed
def view_character(character_id):
    character = Character.query.get_or_404(character_id)
    
    faction_data = g.translations['game']['factions'].get(character.faction, {})
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    battle_logs = BattleLog.query.filter(
        (BattleLog.attacker_id == character.id) |
        (BattleLog.defender_id == character.id)
    ).order_by(BattleLog.timestamp.desc()).paginate(page=page, per_page=per_page)
    
    rankings = get_cached_rankings()
    user_ranks = {
        'xp': next((i+1 for i,c in enumerate(rankings['xp']) if c['id'] == character.id), None),
        'level': next((i+1 for i,c in enumerate(rankings['level']) if c['id'] == character.id), None),
        'kills': next((i+1 for i,c in enumerate(rankings['kills']) if c['id'] == character.id), None),
        'deaths': next((i+1 for i,c in enumerate(rankings['deaths']) if c['id'] == character.id), None)
    }
    
    return render_template('view_character.html',
                         viewed_character=character,
                         faction_data=faction_data,
                         translations=g.translations,
                         current_user=current_user,
                         now=datetime.utcnow(),
                         user_xp_rank=user_ranks['xp'],
                         user_level_rank=user_ranks['level'],
                         user_kills_rank=user_ranks['kills'],
                         user_deaths_rank=user_ranks['deaths'],
                         battle_logs=battle_logs,
                         is_admin=current_user.is_admin)

def update_character_resources(character):
    return character.update_resources()

@game_bp.route('/search-players')
@login_required
@not_jailed
def search_players():
    query = request.args.get('query', '').strip()
    
    if not query or len(query) < 2:
        flash("Please enter at least 2 characters to search")
        return redirect(url_for('game.dashboard'))
    
    normalized_query = normalize_name(query)
    
    characters = Character.query.filter(
        Character.normalized_name.contains(normalized_query)
    ).order_by(Character.level.desc()).limit(20).all()
    
    return render_template('search_results.html',
                         translations=g.translations,
                         results=characters,
                         query=query)

@game_bp.route('/recent-players')
@login_required
@not_jailed
def recent_players():
    recent_chars = Character.query.order_by(
        Character.created_at.desc()
    ).limit(3).all()
    
    return jsonify([{
        'id': char.id,
        'name': char.name,
        'level': char.level,
        'faction': char.faction
    } for char in recent_chars])


@game_bp.route('/dashboard')
@login_required
@not_jailed
def dashboard():
    character = current_user.character
    
    max_hp = character.max_healthpoints
    
    default_heal_cost = 50 
    default_heal_amount = max_hp // 4 
    
    character.healthpoints = max(0, character.healthpoints)
    
    max_possible_heal = max_hp - character.healthpoints
    
    recent_players = []
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    if not current_user.character:
        return redirect(url_for('game.create_character'))
    
    faction_data = None
    if current_user.character and 'game' in g.translations and 'factions' in g.translations['game']:
        faction_key = current_user.character.faction
        faction_data = g.translations['game']['factions'].get(faction_key, {})
    
    max_hp = current_user.character.max_healthpoints
    current_hp = current_user.character.healthpoints
    max_possible_heal = max_hp - current_hp
    default_heal = min(10, max_possible_heal) if max_possible_heal > 0 else 0
    
    json_path = os.path.join(current_app.root_path, 'locales', current_lang, 'game', 'buildings.json')
    with open(json_path, encoding='utf-8') as f:
        buildings_data = json.load(f)
    
    buildings = buildings_data.get("buildings", [])
    
    def get_url_by_key(key):
        return {
            "academy": url_for('game.academy'),
            "graveyard": url_for('game.battlefield'),
            "arena": url_for('game.arena'),
            "inventory": url_for('game.shop'),
            "weaponshop": url_for('game.weapon_shop'),
            "armorshop": url_for('game.armor_shop'),
            "magicshop": url_for('game.magic_shop'),
            "bank": url_for('game.bank'),
            "mail": url_for('game.mailbox'),
            "mine": url_for('game.mine'),
            "gambling": url_for('game.lottery'),
            "hall_of_champions": url_for('game.rankings'),
            "library": "#",
            "mission_board": "#",
            "wishing_well": "#",
            "player_house": "#",
            "tavern": "#"
        }.get(key, "#")
    
    for b in buildings:
        b['url'] = get_url_by_key(b.get('key', '').lower())
    
    return render_template('dashboard.html',
                            translations=g.translations,
                            user=current_user,
                            faction_data=faction_data,
                            current_language=current_lang,
                            max_possible_heal=max_possible_heal,
                            default_heal=default_heal,
                            max_hp=max_hp,
                            buildings=buildings)

                         
@game_bp.app_context_processor
def inject_sidebar_data():
    if not current_user.is_authenticated:
        return {}
    
    online_count = User.query.filter(
        User.last_activity > datetime.utcnow() - timedelta(minutes=5),
        User.character != None
    ).count()
    
    recent_players = Character.query \
        .order_by(Character.created_at.desc()) \
        .limit(3) \
        .all()
        
    unread_count = 0
    if hasattr(current_user, 'character') and current_user.character:
        unread_count = Message.query.filter(
            Message.recipient_id == current_user.character.id,
            Message.is_read == False,
            Message.expires_at > datetime.utcnow()
        ).count()
    
    if not hasattr(current_user, 'character') or current_user.character is None:
        return {
            'user_xp_rank': None,
            'user_level_rank': None,
            'user_kills_rank': None,
            'user_deaths_rank': None,
            'recent_players': recent_players,
            'unread_message_count': 0
        }
    
    rankings = get_cached_rankings()
    char_id = current_user.character.id
    
    user_ranks = {
        'xp': next((i+1 for i,c in enumerate(rankings['xp']) if c['id'] == char_id), None),
        'level': next((i+1 for i,c in enumerate(rankings['level']) if c['id'] == char_id), None),
        'kills': next((i+1 for i,c in enumerate(rankings['kills']) if c['id'] == char_id), None),
        'deaths': next((i+1 for i,c in enumerate(rankings['deaths']) if c['id'] == char_id), None)
    }
    
    return {
        'user_xp_rank': user_ranks['xp'],
        'user_level_rank': user_ranks['level'],
        'user_kills_rank': user_ranks['kills'],
        'user_deaths_rank': user_ranks['deaths'],
        'recent_players': recent_players,
        'online_count': online_count,
        'unread_message_count': unread_count,
        'character_data': {  
            'max_hp': current_user.character.max_healthpoints,
            'current_hp': current_user.character.healthpoints,
            'gold': current_user.character.gold,
            'is_dead': current_user.character.is_dead
        }
    }

@game_bp.route('/fight/<int:opponent_id>')
@login_required
@not_jailed
def fight(opponent_id):
    if not current_user.character:
        flash("You need a character to fight!", 'error')
        return redirect(url_for('game.dashboard'))
    
    if opponent_id == current_user.character.id:
        flash("Are you trying to hurt yourself? That is not allowed!", 'error')
        return redirect(url_for('game.dashboard'))
    
    opponent = Character.query.get_or_404(opponent_id)
    attacker = current_user.character
    
    min_attackable_level = get_min_attackable_level(attacker.level)
    if opponent.level < min_attackable_level:
        flash(f"You cannot attack players below level {min_attackable_level}!", 'error')
        return redirect(url_for('game.view_character', character_id=opponent_id))
    
    if attacker.is_dead:
        flash("You're dead! You need to heal before fighting.", 'error')
        return redirect(url_for('game.dashboard'))
    
    if opponent.is_dead:
        flash(f"{opponent.name} is already defeated!", 'error')
        return redirect(url_for('game.view_character', character_id=opponent_id))
    
    existing_battle = BattleLog.query.filter(
        ((BattleLog.attacker_id == current_user.character.id) & 
         (BattleLog.defender_id == opponent.id)) | 
        ((BattleLog.attacker_id == opponent.id) & 
         (BattleLog.defender_id == current_user.character.id))
    ).order_by(BattleLog.timestamp.desc()).first()
    
    if existing_battle:
        if existing_battle.winner_id:
            time_since_battle = (datetime.utcnow() - existing_battle.timestamp).total_seconds()
            if time_since_battle < 300:
                flash(f"You recently battled {opponent.name}. Please wait before fighting again.", 'error')
                return redirect(url_for('game.view_character', character_id=opponent_id))
        else:
            flash("You already have an ongoing battle with this opponent!", 'error')
            return redirect(url_for('game.view_character', character_id=opponent_id))
    
    return render_template('fight.html',
                         translations=g.translations,
                         opponent=opponent,
                         current_user=current_user)

@game_bp.route('/heal', methods=['POST'])
@login_required
@not_jailed
def heal():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    try:
        amount = int(request.form.get('amount', 0))
    except ValueError:
        flash("Invalid healing amount!", 'error')
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    max_hp = character.max_healthpoints
    
    if amount <= 0:
        flash("Healing amount must be positive!", 'error')
        return redirect(url_for('game.dashboard'))
    
    if character.is_dead:
        revive_cost = max_hp - character.healthpoints
        if amount != revive_cost:
            flash(f"To revive, you must heal exactly {revive_cost} HP!", 'error')
            return redirect(url_for('game.dashboard'))
        
        if character.gold >= revive_cost:
            character.heal(revive_cost, revive_cost)
            character.is_dead = False
            db.session.commit()
            flash("You've been revived to full health!", 'success')
        else:
            flash(f"You need {revive_cost} gold to revive!", 'error')
        return redirect(url_for('game.dashboard'))
    
    if character.healthpoints >= max_hp:
        flash("You're already at full health!", 'info')
        return redirect(url_for('game.dashboard'))
    
    max_possible_heal = max_hp - character.healthpoints
    actual_heal = min(amount, max_possible_heal)
    cost = actual_heal
    
    if character.gold >= cost:
        character.heal(actual_heal, cost)
        db.session.commit()
        flash(f"Healed for {actual_heal} HP! Cost: {cost} gold. (Current HP: {character.healthpoints}/{max_hp})", 'success')
    else:
        affordable_heal = min(character.gold, max_possible_heal)
        if affordable_heal > 0:
            flash(f"Not enough gold for full heal! You can heal up to {affordable_heal} HP with your current gold.", 'warning')
        else:
            flash("Not enough gold to heal!", 'error')
    
    return redirect(url_for('game.dashboard'))

    
@game_bp.route('/rankings')
@login_required
@not_jailed
def rankings():
    current_lang = get_current_language()
    translations = load_translations(current_lang)

    translations.setdefault('dashboard', {})
    translations.setdefault('rankings', {})
    translations.setdefault('game', {}).setdefault('factions', {})
    
    data = get_cached_rankings()
    
    top_forca = Character.query.order_by(Character.forca.desc()).limit(10).all()
    top_destreza = Character.query.order_by(Character.destreza.desc()).limit(10).all()
    top_inteligencia = Character.query.order_by(Character.inteligencia.desc()).limit(10).all()
    top_devocao = Character.query.order_by(Character.devocao.desc()).limit(10).all()
    
    combined_query = db.session.query(
        Character,
        (Character.forca + Character.destreza + Character.inteligencia + Character.devocao).label('combined')
    ).order_by(db.desc('combined')).limit(10).all()
    top_combined = [char for char, _ in combined_query]
    
    top_reputation = Character.query.order_by(Character.reputation.desc()).limit(10).all()
    worst_reputation = Character.query.order_by(Character.reputation.asc()).limit(10).all()
    
    for char in (top_forca + top_destreza + top_inteligencia + top_devocao + 
                top_combined + top_reputation + worst_reputation):
        if not hasattr(char, 'faction_image'):
            char.faction_image = f"{char.faction.lower()}.webp"
    
    return render_template('rankings.html',
        translations=translations,
        top_xp=data['xp'][:10],
        top_level=data['level'][:10],
        top_kills=data['kills'][:10],
        top_deaths=data['deaths'][:10],
        top_forca=top_forca,
        top_destreza=top_destreza,
        top_inteligencia=top_inteligencia,
        top_devocao=top_devocao,
        top_combined=top_combined,
        top_reputation=top_reputation,
        worst_reputation=worst_reputation,
        faction_kills=data['faction_kills'],
        faction_deaths=data['faction_deaths'],
        faction_count=data['faction_count'],
        faction_pie=data['faction_pie'],
        last_updated=data['timestamp'],
        current_language=current_lang,
        gold=data['gold'],
        bank_gold=data['bank_gold'],
        mining_level=data['mining_level'],
        diamonds=data['diamonds']
    )
    
@game_bp.route('/battle-log/<int:battle_id>')
@login_required
@not_jailed
def battle_log(battle_id):
    battle = BattleLog.query.get_or_404(battle_id)
    
    if (current_user.character.id != battle.attacker_id and 
        current_user.character.id != battle.defender_id):
        flash("You can only view battles you participated in", 'error')
        return redirect(url_for('game.dashboard'))
    
    return render_template('battle_log.html',
                         battle=battle,
                         translations=g.translations)

@game_bp.route('/process-fight/<int:opponent_id>', methods=['POST'])
@login_required
@not_jailed
def process_fight(opponent_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
        
    attacker = current_user.character
    if attacker.resource < 1:
        flash("You need at least 1 resource to attack!", 'error')
        return redirect(url_for('game.view_character', character_id=opponent_id))
    
    attacker.resource -= 1    
    
    if opponent_id == current_user.character.id:
        flash("Are you trying to hurt yourself? That is not allowed!", 'error')
        return redirect(url_for('game.dashboard'))
    
    opponent = Character.query.get_or_404(opponent_id)
    
    min_attackable_level = get_min_attackable_level(attacker.level)
    if opponent.level < min_attackable_level:
        flash(f"You cannot attack players below level {min_attackable_level}!", 'error')
        return redirect(url_for('game.view_character', character_id=opponent_id))
    
    if current_user.character.is_dead:
        flash("You're dead! You need to heal before fighting.", 'error')
        return redirect(url_for('game.dashboard'))
    
    if opponent.is_dead:
        flash(f"{opponent.name} is already defeated!", 'error')
        return redirect(url_for('game.view_character', character_id=opponent_id))
    
    existing_battle = BattleLog.query.filter(
        ((BattleLog.attacker_id == current_user.character.id) & 
         (BattleLog.defender_id == opponent.id)) | 
        ((BattleLog.attacker_id == opponent.id) & 
         (BattleLog.defender_id == current_user.character.id))
    ).order_by(BattleLog.timestamp.desc()).first()
    
    if existing_battle:
        if existing_battle.winner_id:
            time_since_battle = (datetime.utcnow() - existing_battle.timestamp).total_seconds()
            if time_since_battle < 300:
                flash(f"You recently battled {opponent.name}. Please wait before fighting again.", 'error')
                return redirect(url_for('game.view_character', character_id=opponent_id))
        else:
            flash("You already have an ongoing battle with this opponent!", 'error')
            return redirect(url_for('game.view_character', character_id=opponent_id))
    
    fight_log = []
    attacker = current_user.character
    defender = opponent
    
    attacker_initial_rep = attacker.reputation
    defender_initial_rep = defender.reputation
    
    same_faction = attacker.faction == defender.faction
    enemy_faction = get_enemy_faction(attacker.faction) == defender.faction.lower()
    
    def calculate_defense(character):
        defense = 0
        if isinstance(character, NPC):
            if character.armor and 'defense' in character.armor.stats:
                defense = character.armor.stats['defense']
        else:
            for item in character.items:
                if item.equipped and item.item.item_type == 'armor' and 'defense' in item.item.stats:
                    defense += item.item.stats['defense']
        return defense
    
    attacker_defense = calculate_defense(attacker)
    defender_defense = calculate_defense(defender)
    
    def get_defense_chance(attacker, defender, is_defender_urghan):
        base_chance = 5
        
        if is_defender_urghan:
            base_chance = 15
            
        if attacker.forca > defender.forca:
            if is_defender_urghan:
                if attacker.faction.lower() == 'urghan':
                    base_chance = 10
                else:
                    base_chance = 10
            else:
                base_chance = 10
        elif attacker.forca == defender.forca:
            if is_defender_urghan:
                base_chance = 15
            else:
                base_chance = 5
                
        str_diff = abs(attacker.forca - defender.forca)
        str_diff_bonus = (str_diff // 10) * 2
        
        if is_defender_urghan:
            str_diff_bonus = min(str_diff_bonus, 40)
        else:
            str_diff_bonus = min(str_diff_bonus, 25)
            
        if defender.forca > attacker.forca:
            base_chance += str_diff_bonus
        else:
            base_chance -= str_diff_bonus
            
        return max(0, min(base_chance, 95))
    
    is_defender_urghan = defender.faction.lower() == 'urghan'
    defense_chance = get_defense_chance(attacker, defender, is_defender_urghan)
    
    def get_dodge_chance(character, opponent):
        base_chance = 5
        if character.faction.lower() == 'veylan':
            base_chance = 10
            
        dex_diff = character.destreza - opponent.destreza
        if dex_diff > 0:
            dex_diff_bonus = (dex_diff // 10) * 2
            
            if character.faction.lower() == 'veylan':
                dex_diff_bonus = min(dex_diff_bonus, 40)
            else:
                dex_diff_bonus = min(dex_diff_bonus, 25)
                
            base_chance += dex_diff_bonus
            
        return max(0, min(base_chance, 95))
    
    attacker_dodge = get_dodge_chance(attacker, defender)
    defender_dodge = get_dodge_chance(defender, attacker)
    
    def get_crit_chance(character, opponent):
        base_chance = 5
        if character.faction.lower() == 'aureen':
            base_chance = 10
            
        if character.inteligencia > opponent.inteligencia:
            base_chance += 5
            
        int_diff = character.inteligencia - opponent.inteligencia
        if int_diff > 0:
            int_diff_bonus = (int_diff // 10) * (1 if character.faction.lower() == 'aureen' else 0.5)
            
            if character.faction.lower() == 'aureen':
                int_diff_bonus = min(int_diff_bonus, 25)
            else:
                int_diff_bonus = min(int_diff_bonus, 20)
                
            base_chance += int_diff_bonus
            
        return max(0, min(base_chance, 95))
    
    attacker_crit = get_crit_chance(attacker, defender)
    defender_crit = get_crit_chance(defender, attacker)
    
    def get_heal_chance(character, opponent):
        base_chance = 2
        if character.faction.lower() == 'camyra':
            base_chance = 10
            
        if character.devocao > opponent.devocao:
            base_chance += 5
            
        dev_diff = character.devocao - opponent.devocao
        if dev_diff > 0:
            dev_diff_bonus = (dev_diff // 10) * (1 if character.faction.lower() == 'camyra' else 0.5)
            
            if character.faction.lower() == 'camyra':
                dev_diff_bonus = min(dev_diff_bonus, 30)
            else:
                dev_diff_bonus = min(dev_diff_bonus, 20)
                
            base_chance += dev_diff_bonus
            
        return max(0, min(base_chance, 95))
    
    attacker_heal = get_heal_chance(attacker, defender)
    defender_heal = get_heal_chance(defender, attacker)
    
    attacker_first = attacker.destreza >= defender.destreza
    
    while True:
        current_attacker = attacker if attacker_first else defender
        current_defender = defender if attacker_first else attacker
        
        dodge_chance = attacker_dodge if current_attacker == attacker else defender_dodge
        if random.random() < dodge_chance / 100:
            fight_log.append(Markup(f"{current_defender.name} <span class='dodge'>dodged</span> {current_attacker.name}'s attack!"))
            attacker_first = not attacker_first
            continue
            
        damage, weapon_name = calculate_damage(current_attacker, current_defender)
        
        crit_chance = attacker_crit if current_attacker == attacker else defender_crit
        is_crit = random.random() < crit_chance / 100
        if is_crit:
            base_damage = int(damage * 1.5)
            
        defense_chance = get_defense_chance(current_attacker, current_defender, current_defender.faction.lower() == 'urghan')
        defense_amount = int(current_defender.forca + (defender_defense if current_defender == defender else attacker_defense))
        is_blocked = random.random() < defense_chance / 100

        if is_blocked:
            damage = max(0, int(damage - defense_amount))
            fight_log.append(Markup(f"{current_defender.name} <span class='block'>{'blocked' if damage == 0 else 'partially blocked'}</span> " f"{damage + defense_amount} damage (reduced by {int(defense_amount)})!"))
            
        if damage > 0:
            attack_verb = "strikes" if weapon_name != "bare hands" else "hits"
            if is_crit:
                fight_log.append(Markup(f"{current_attacker.name} {attack_verb} {current_defender.name} with {weapon_name} for  " f"<span class='crit'>{damage} critical damage</span>!"))
            else:
                fight_log.append(Markup(f"{current_attacker.name} {attack_verb} {current_defender.name} with {weapon_name} for " f"<span class='damage'>{damage} damage</span>!"))
            
            current_defender.healthpoints = int(current_defender.healthpoints - damage)
            
            heal_chance = attacker_heal if current_defender == attacker else defender_heal
            if random.random() < heal_chance / 100:
                heal_percent = 0.1 
                if current_defender.faction.lower() == 'camyra':
                    dev_diff = current_defender.devocao - current_attacker.devocao
                    heal_percent += min(0.5, (dev_diff // 10) * 0.01)
                
                heal_amount = int(current_defender.max_healthpoints * heal_percent)
                current_defender.healthpoints = int(min(current_defender.max_healthpoints, current_defender.healthpoints + heal_amount))
                fight_log.append(Markup(f"{current_defender.name} <span class='heal'>healed</span> for " f"<span class='heal-amount'>{heal_amount} HP</span>!"))
        
        if current_defender.healthpoints <= 0:
            current_defender.healthpoints = 0
            current_defender.is_dead = True
            current_defender.deaths += 1
            current_attacker.pvp_kills += 1
            current_attacker.last_killed = current_defender
            current_defender.last_killed_by = current_attacker

            base_xp = random.randint(1, 100)
            level_diff = current_defender.level - current_attacker.level
            bonus_xp = max(0, level_diff) * 10
            total_xp = base_xp + bonus_xp
            current_attacker.add_xp(total_xp)
            
            gold_loss = min(current_defender.gold, int(current_defender.gold * 0.05))  # 5% of on-hand gold
            current_defender.gold -= gold_loss
            current_attacker.gold += gold_loss
            
            fight_log.append(f"{current_attacker.name} has defeated {current_defender.name} and gained {total_xp} XP!")
            winner = current_attacker
            
            if same_faction:
                if winner == attacker:
                    attacker.reputation -= 4
                else:
                    attacker.reputation -= 4
                    defender.reputation += 4
            elif enemy_faction:
                if winner == attacker:
                    attacker.reputation += 3
                    defender.reputation -= 3
                else:
                    attacker.reputation -= 3
                    defender.reputation += 3
            else:
                if winner == attacker:
                    attacker.reputation += 1
                    defender.reputation -= 1
                else:
                    attacker.reputation -= 1
                    defender.reputation += 1
    
            break

        attacker_first = not attacker_first
    
    attacker.last_fight_time = datetime.utcnow()
    
    attacker_rep_change = attacker.reputation - attacker_initial_rep
    defender_rep_change = defender.reputation - defender_initial_rep
    
    original_log = []
    for entry in fight_log:
        original_entry = entry.replace("You", attacker.name if "You hit" in entry else defender.name)
        original_entry = original_entry.replace("you", defender.name if "hits you" in original_entry else attacker.name)
        original_log.append(original_entry)
    
    battle = BattleLog(
        attacker_id=attacker.id,
        defender_id=defender.id,
        winner_id=winner.id,
        log=original_log,
        attacker_name=attacker.name,
        defender_name=defender.name,
        winner_name=winner.name,
        attacker_faction=attacker.faction,
        defender_faction=defender.faction,
        winner_faction=winner.faction,
        attacker_level=attacker.level,
        defender_level=defender.level,
        attacker_reputation_change=attacker_rep_change,
        defender_reputation_change=defender_rep_change,
        xp_gained=total_xp,
        resource_cost=1,
        attacker_gold_change=gold_loss if winner == attacker else -gold_loss,
        defender_gold_change=-gold_loss if winner == attacker else gold_loss
    )
    
    db.session.add(battle)
    db.session.flush()
    
    attacker_message = Message(
        sender_id=winner.id,
        recipient_id=attacker.id,
        subject=f"Battle Result vs {defender.name}",
        body=f"You {'won' if winner == attacker else 'lost'} the battle against {defender.name}.\n\n" +
             f"View battle log: {url_for('game.battle_log', battle_id=battle.id, _external=True)}"
    )
    
    defender_message = Message(
        sender_id=winner.id,
        recipient_id=defender.id,
        subject=f"Battle Result vs {attacker.name}",
        body=f"You {'won' if winner == defender else 'lost'} the battle against {attacker.name}.\n\n" +
             f"View battle log: {url_for('game.battle_log', battle_id=battle.id, _external=True)}"
    )
    
    db.session.add(attacker_message)
    db.session.add(defender_message)
    db.session.commit()    
     
    return render_template('fight_result.html',
                         translations=g.translations,
                         fight_log=fight_log,
                         winner=winner,
                         current_user=current_user,
                         opponent=opponent,
                         battle_id=battle.id,
                         attacker_rep_change=attacker_rep_change,
                         defender_rep_change=defender_rep_change,
                         gold_loss=gold_loss
                         )
                         
@game_bp.route('/my-battles')
@login_required
@not_jailed
def my_battles():
    battles = BattleLog.query.filter(
        (BattleLog.attacker_id == current_user.character.id) |
        (BattleLog.defender_id == current_user.character.id)
    ).order_by(BattleLog.timestamp.desc()).all()
    
    return render_template('my_battles.html',
                         battles=battles,
                         translations=g.translations)
# Player inventory:
@game_bp.route('/shop')
@login_required
@not_jailed
def shop():
    current_tab = request.args.get('tab', 'weapons') 
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    inventory = db.session.query(
        Item,
        db.func.count(CharacterItem.id).label('count')
    ).join(
        CharacterItem,
        CharacterItem.item_id == Item.id
    ).filter(
        CharacterItem.character_id == current_user.character.id
    ).group_by(Item.id).all()
    
    weapons = [(item, count) for item, count in inventory if item.item_type == 'weapon']
    magic_items = [(item, count) for item, count in inventory if item.item_type == 'magic']
    
    armor_by_slot = {
        'head': [],
        'body': [],
        'gloves': [],
        'pants': [],
        'boots': []
    }
    
    for item, count in inventory:
        if item.item_type == 'armor' and item.armor_type in armor_by_slot:
            armor_by_slot[item.armor_type].append((item, count))
    
    equipped_items = {item.item_id for item in current_user.character.items if item.equipped}
    
    return render_template('shop.html',
                         current_tab=current_tab,
                         translations=g.translations,
                         weapons=weapons,
                         armor_by_slot=armor_by_slot,
                         magic_items=magic_items,
                         equipped_items=equipped_items,
                         character=current_user.character)

@game_bp.route('/buy-item/<int:item_id>', methods=['POST'])
@login_required
@not_jailed
def buy_item(item_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    item = Item.query.get_or_404(item_id)
    character = current_user.character
    source = request.form.get('source', 'shop')
    
    if item.price_type == 'diamonds':
        if character.diamonds < item.price:
            flash(g.translations['shop']['not_enough_diamonds'], 'error')
            return redirect(url_for(f'game.{source}'))
    else:
        if character.gold < item.price:
            flash(g.translations['shop']['not_enough_gold'], 'error')
            return redirect(url_for(f'game.{source}'))
    
    if character.level < item.min_level:
        flash(g.translations['shop']['level_too_low'], 'error')
        return redirect(url_for(f'game.{source}'))
    
    try:
        if item.price_type == 'diamonds':
            character.diamonds -= item.price
        else:
            character.gold -= item.price
        
        character_item = CharacterItem(
            character_id=character.id,
            item_id=item.id,
            equipped=False
        )
        
        db.session.add(character_item)
        db.session.commit()
        
        flash(g.translations['shop']['purchase_success'], 'success')
    except Exception as e:
        db.session.rollback()
        flash("An error occurred during the purchase", 'error')
    
    return redirect(url_for(f'game.{source}'))

@game_bp.route('/equip-item/<int:item_id>', methods=['POST'])
@login_required
@not_jailed
def equip_item(item_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    character_item = CharacterItem.query.filter_by(
        character_id=character.id,
        item_id=item_id
    ).first_or_404()
    
    item = character_item.item
    
    if character.level < item.min_level:
        flash(f"You need to be at least level {item.min_level} to equip this item", 'error')
        current_tab = request.form.get('current_tab', 'weapons')
        return redirect(url_for('game.shop', tab=current_tab))
    
    if item.required_attribute and item.required_amount > 0:
        attr_value = getattr(character, item.required_attribute, 0)
        if attr_value < item.required_amount:
            flash(f"You need at least {item.required_amount} {item.required_attribute} to equip this item", 'error')
            current_tab = request.form.get('current_tab', 'weapons')
            return redirect(url_for('game.shop', tab=current_tab))
    
    if item.item_type == 'weapon':
        for ci in character.items:
            if ci.equipped and ci.item.item_type == 'weapon':
                ci.equipped = False
    else:
        for ci in character.items:
            if ci.equipped and ci.item.item_type == 'armor' and ci.item.armor_type == item.armor_type:
                ci.equipped = False
    
    character_item.equipped = True
    
    if item.item_type == 'armor' and 'health_bonus' in item.stats:
        character.healthpoints = min(character.healthpoints, character.max_healthpoints)
    
    db.session.commit()
    
    flash(g.translations['shop']['equip_success'], 'success')
    current_tab = request.form.get('current_tab', 'weapons')
    return redirect(url_for('game.shop', tab=current_tab))

@game_bp.route('/unequip-item/<int:item_id>', methods=['POST'])
@login_required
@not_jailed
def unequip_item(item_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    character_item = CharacterItem.query.filter_by(
        character_id=character.id,
        item_id=item_id
    ).first_or_404()
    
    item = character_item.item
    
    if character_item.equipped:
        character_item.equipped = False
        
        if item.item_type == 'armor' and 'health_bonus' in item.stats:
            character.healthpoints = min(character.healthpoints, character.max_healthpoints)
        
        db.session.commit()
        flash(g.translations['shop']['unequip_success'], 'success')
    else:
        flash("Item is not equipped", 'error')
    
    current_tab = request.form.get('current_tab', 'weapons')
    return redirect(url_for('game.shop', tab=current_tab))
    

@game_bp.route('/sell-item/<int:item_id>', methods=['POST'])
@login_required
@not_jailed
def sell_item(item_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    character_item = CharacterItem.query.filter_by(
        character_id=character.id,
        item_id=item_id
    ).first_or_404()
    
    source = request.form.get('source', 'shop')
    
    sell_price = character_item.item.price // 4
    
    character.gold += sell_price
    db.session.delete(character_item)
    db.session.commit()
    
    flash(g.translations['shop']['sell_success'], 'success')
    current_tab = request.form.get('current_tab', 'weapons')
    return redirect(url_for(f'game.{source}', tab=current_tab))
    
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access this page", 'error')
            return redirect(url_for('auth.login'))
            
        print(f"Admin check for {current_user.username}: is_admin={current_user.is_admin}")
        
        if not current_user.is_admin:
            flash("Access denied - administrators only", 'error')
            return redirect(url_for('game.dashboard'))
            
        return f(*args, **kwargs)
    return decorated_function

@game_bp.route('/admin')
@login_required
@admin_required
def admin_panel():
    duplicate_ips = get_duplicate_ips()
    return render_template('admin_panel.html', translations=g.translations, duplicate_ips=duplicate_ips, warning_count=len(duplicate_ips))
    


@game_bp.route('/admin/items')
@login_required
@admin_required
def manage_items():
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    if not isinstance(g.translations, dict):
        g.translations = {}
    
    if 'game' not in g.translations or not isinstance(g.translations['game'], dict):
        g.translations['game'] = {}
    
    if 'items' not in g.translations['game']:
        g.translations['game']['items'] = {
            'weapon': {},
            'armor': {
                'head': {},
                'body': {},
                'gloves': {},
                'pants': {},
                'boots': {}
            }
        }
    elif callable(g.translations['game']['items']):
        g.translations['game']['items'] = g.translations['game']['items']()
    elif not isinstance(g.translations['game']['items'], dict):
        g.translations['game']['items'] = {
            'weapon': {},
            'armor': {
                'head': {},
                'body': {},
                'gloves': {},
                'pants': {},
                'boots': {}
            }
        }
    
    if 'weapon' not in g.translations['game']['items'] or not isinstance(g.translations['game']['items']['weapon'], dict):
        g.translations['game']['items']['weapon'] = {}
    
    if 'armor' not in g.translations['game']['items'] or not isinstance(g.translations['game']['items']['armor'], dict):
        g.translations['game']['items']['armor'] = {
            'head': {},
            'body': {},
            'gloves': {},
            'pants': {},
            'boots': {}
        }
    
    for slot in ['head', 'body', 'gloves', 'pants', 'boots']:
        if slot not in g.translations['game']['items']['armor'] or not isinstance(g.translations['game']['items']['armor'][slot], dict):
            g.translations['game']['items']['armor'][slot] = {}
    
    items = Item.query.order_by(Item.item_type, Item.min_level).all()
    return render_template('admin_items.html',
                        translations=g.translations,
                        items=items)

@game_bp.route('/admin/items/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_item():
    if request.method == 'POST':
        try:
            item_type = request.form['item_type']
            translation_key = request.form['translation_key']
            price = int(request.form['price'])
            price_type = request.form.get('price_type', 'gold')
            min_level = int(request.form['min_level'])
            is_npc_only = 'is_npc_only' in request.form
            is_rare_drop = 'is_rare_drop' in request.form if is_npc_only else False
            drop_rate = float(request.form.get('drop_rate', 0.00003)) if is_rare_drop else 0.0
            
            required_attribute = request.form.get('required_attribute')
            if required_attribute == 'none':
                required_attribute = None
            required_amount = int(request.form.get('required_amount', 0))
            
            stats = {}
            if item_type == 'weapon':
                dice_count = int(request.form['dice_count'])
                dice_type = int(request.form['dice_type'])
                stats['damage'] = f"{dice_count}d{dice_type}"
                armor_type = None
            elif item_type == 'armor':
                stats['defense'] = int(request.form['defense'])
                stats['health_bonus'] = int(request.form.get('health_bonus', 0))
                armor_type = request.form['armor_type']
            
            new_item = Item(
                item_type=item_type,
                armor_type=armor_type,
                translation_key=translation_key,
                price=price,
                price_type=price_type,
                min_level=min_level,
                stats=stats,
                required_attribute=required_attribute,
                required_amount=required_amount,
                is_npc_only=is_npc_only,
                is_rare_drop=is_rare_drop,
                drop_rate=drop_rate
            )
            
            db.session.add(new_item)
            db.session.commit()
            
            update_item_translations(new_item)
            flash(g.translations['admin']['item_added'], 'success')
            return redirect(url_for('game.manage_items'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding item: {str(e)}", 'error')
    
    return render_template('admin_add_item.html', translations=g.translations)

@game_bp.route('/admin/items/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_item(item_id):
    item = Item.query.get_or_404(item_id)
    
    if request.method == 'POST':
        try:
            old_translation_key = item.translation_key
            item.translation_key = request.form['translation_key']
            item.price = int(request.form['price'])
            item.price_type = request.form.get('price_type', 'gold')  # Update price type
            item.min_level = int(request.form['min_level'])
            item.is_npc_only = 'is_npc_only' in request.form
            item.is_rare_drop = 'is_rare_drop' in request.form if item.is_npc_only else False
            item.drop_rate = float(request.form.get('drop_rate', 0.00003)) if item.is_rare_drop else 0.0
            
            if item.item_type == 'weapon':
                dice_count = int(request.form.get('dice_count', 1))
                dice_type = int(request.form.get('dice_type', 6))
                item.stats = {
                    'damage': f"{dice_count}d{dice_type}"
                }
                item.armor_type = None
            elif item.item_type == 'armor':
                item.stats = {
                    'defense': int(request.form.get('defense', 1)),
                    'health_bonus': int(request.form.get('health_bonus', 0))
                }
                item.armor_type = request.form['armor_type']
            
            db.session.commit()
            
            if old_translation_key != item.translation_key:
                update_item_translations(item)
                remove_item_translation(old_translation_key, item.item_type)
            
            flash(g.translations['admin']['item_updated'], 'success')
            return redirect(url_for('game.manage_items'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating item: {str(e)}")
            flash(f"Error updating item: {str(e)}", 'error')
    
    dice_count = 1
    dice_type = 6
    if item.item_type == 'weapon' and 'damage' in item.stats:
        damage_parts = item.stats['damage'].split('d')
        if len(damage_parts) == 2:
            dice_count = int(damage_parts[0])
            dice_type = int(damage_parts[1])
    
    return render_template('admin_edit_item.html',
                         translations=g.translations,
                         item=item,
                         dice_count=dice_count,
                         dice_type=dice_type)

def update_item_translations(item):
    """Update the translations JSON file with new item data"""
    try:
        lang = get_current_language()
        base_path = os.path.join(current_app.root_path, 'locales', lang)
        
        if item.item_type == 'weapon':
            file_path = os.path.join(base_path, 'game', 'items', 'weapons.json')
        elif item.item_type == 'armor':
            file_path = os.path.join(base_path, 'game', 'items', 'armor.json')
        elif item.item_type == 'magic':
            file_path = os.path.join(base_path, 'game', 'items', 'magic.json')
        else:
            return
            
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
        translations = {}
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                translations = json.load(f)
        
        friendly_name = item.translation_key.replace('_', ' ').title()
        
        translations[item.translation_key] = {
            'name': friendly_name,
            'description': f"A {friendly_name.lower()}"
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to update translations: {str(e)}")
        raise
        
def remove_item_translation(translation_key, item_type):
    """Remove an item from the translations file"""
    try:
        lang = get_current_language()
        base_path = os.path.join(current_app.root_path, 'locales', lang)
        
        if item_type == 'weapon':
            file_path = os.path.join(base_path, 'game', 'items', 'weapons.json')
        elif item_type == 'armor':
            file_path = os.path.join(base_path, 'game', 'items', 'armor.json')
        elif item_type == 'magic':
            file_path = os.path.join(base_path, 'game', 'items', 'magic.json')
        else:
            return
            
        if not os.path.exists(file_path):
            return
            
        with open(file_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        
        translations.pop(translation_key, None)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to remove translation: {str(e)}")

@game_bp.route('/admin/items/delete/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def delete_item(item_id):
    item = Item.query.get_or_404(item_id)
    
    CharacterItem.query.filter_by(item_id=item.id).delete()
    
    db.session.delete(item)
    db.session.commit()
    
    flash(g.translations['admin']['item_deleted'], 'success')
    return redirect(url_for('game.manage_items'))

@game_bp.route('/admin/users')
@login_required
@admin_required
def manage_users():
    users = User.query.order_by(User.username).all()
    duplicate_ips = get_duplicate_ips()
    
    duplicate_ip_list = list(duplicate_ips.keys())
    
    return render_template('admin_users.html',
                         translations=g.translations,
                         users=users,
                         duplicate_ips=duplicate_ip_list)

@game_bp.route('/admin/users/toggle-admin/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id and user.is_admin:
        admins = User.query.filter_by(is_admin=True).count()
        if admins <= 1:
            flash("Não é possível remover o último admin", 'error')
            return redirect(url_for('game.manage_users'))
    
    user.is_admin = not user.is_admin
    db.session.commit()
    
    if user.is_admin:
        flash(g.translations['admin']['admin_promoted'], 'success')
    else:
        flash(g.translations['admin']['admin_revoked'], 'success')
    
    return redirect(url_for('game.manage_users'))

@game_bp.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            user.username = request.form['username']
            user.language = request.form['language']
            user.is_admin = 'is_admin' in request.form
            
            if request.form.get('password'):
                user.set_password(request.form['password'])
            
            db.session.commit()
            flash(g.translations['admin']['user_updated'], 'success')
            return redirect(url_for('game.manage_users'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating user: {str(e)}", 'error')
    
    duplicate_accounts = []
    if user.ip_address:
        duplicate_accounts = User.query.filter_by(ip_address=user.ip_address).all()
    
    battle_logs = []
    last_killed = None
    last_killed_by = None
    last_killed_battle = None
    last_killed_by_battle = None
    
    if user.character:
        battle_logs = BattleLog.query.filter(
            (BattleLog.attacker_id == user.character.id) |
            (BattleLog.defender_id == user.character.id)
        ).order_by(BattleLog.timestamp.desc()).limit(10).all()
        
        last_killed_battle = BattleLog.query.filter(
            BattleLog.attacker_id == user.character.id,
            BattleLog.winner_id == user.character.id
        ).order_by(BattleLog.timestamp.desc()).first()
        
        if last_killed_battle:
            last_killed = Character.query.get(last_killed_battle.defender_id)
        
        last_killed_by_battle = BattleLog.query.filter(
            BattleLog.defender_id == user.character.id,
            BattleLog.winner_id != user.character.id
        ).order_by(BattleLog.timestamp.desc()).first()
        
        if last_killed_by_battle:
            last_killed_by = Character.query.get(last_killed_by_battle.attacker_id)
    
    all_items = Item.query.order_by(Item.translation_key).all()
    
    return render_template('admin_edit_user.html',
                         translations=g.translations,
                         user=user,
                         duplicate_accounts=duplicate_accounts,
                         battle_logs=battle_logs,
                         last_killed=last_killed,
                         last_killed_by=last_killed_by,
                         last_killed_battle=last_killed_battle,
                         last_killed_by_battle=last_killed_by_battle,
                         all_items=all_items,
                         config=current_app.config)

@game_bp.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    if user_id == current_user.id:
        flash("Você não pode deletar sua própria conta", 'error')
        return redirect(url_for('game.manage_users'))
    
    user = User.query.get_or_404(user_id)
    
    db.session.delete(user)
    db.session.commit()
    
    flash(g.translations['admin']['user_deleted'], 'success')
    return redirect(url_for('game.manage_users'))   

@game_bp.route('/admin/quests')
@login_required
@admin_required
def manage_quests():
    quests = Quest.query.order_by(Quest.created_at.desc()).all()
    npcs = NPC.query.all()
    items = Item.query.all()
    factions = list(g.translations['game']['factions'].keys())
    
    return render_template('admin_quests.html',
                         translations=g.translations,
                         quests=quests,
                         npcs=npcs,
                         items=items,
                         factions=factions)

@game_bp.route('/admin/quests/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_quest():
    if request.method == 'POST':
        try:
            title = request.form['title']
            description = request.form['description']
            translation_key = request.form['translation_key']

            is_unique = 'is_unique' in request.form
            is_active = 'is_active' in request.form
            spawn_chance = float(request.form.get('spawn_chance', 1.0))

            quest = Quest(
                translation_key=translation_key,
                is_unique=is_unique,
                is_active=is_active,
                spawn_chance=spawn_chance
            )
            db.session.add(quest)
            db.session.flush()
            objectives_data = {}
            for key, value in request.form.items():
                if key.startswith('objectives[') and key.endswith('][type]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['type'] = value
                elif key.startswith('objectives[') and key.endswith('][target]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['target'] = value
                elif key.startswith('objectives[') and key.endswith('][amount]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['amount'] = value
                elif key.startswith('objectives[') and key.endswith('][attribute_type]'): # NEW: Capture attribute_type
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['attribute'] = value # Store it under a new key 'attribute'

            for index in sorted(objectives_data.keys()):
                obj_data = objectives_data[index]
                obj_type = obj_data.get('type')
                #obj_target = obj_data.get('target')
                obj_amount = obj_data.get('amount')
                obj_target_value = obj_data.get('target') # Value from generic target field
                obj_attribute_value = obj_data.get('attribute') # Value from attribute select field

                if not obj_type or not obj_amount:
                    continue
                    
                final_target_value = None
                if obj_type == 'train_attribute':
                    final_target_value = obj_attribute_value # Use attribute_type for train_attribute
                else:
                    final_target_value = obj_target_value # Use target for others

                objective = QuestObjective(
                    quest_id=quest.id,
                    objective_type=obj_type,
                    target_value=final_target_value,
                    amount_required=int(obj_amount)
                )
                db.session.add(objective)

            rewards_data = {}
            for key, value in request.form.items():
                if key.startswith('rewards[') and key.endswith('][type]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['type'] = value
                elif key.startswith('rewards[') and key.endswith('][amount]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['amount'] = float(value)
                elif key.startswith('rewards[') and key.endswith('][item]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['item'] = value
                elif key.startswith('rewards[') and key.endswith('][attribute_type]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['attribute_type'] = value

            for index in sorted(rewards_data.keys()):
                rew_data = rewards_data[index]
                rew_type = rew_data.get('type')
                rew_amount = rew_data.get('amount')
                rew_item = rew_data.get('item')
                rew_attribute = rew_data.get('attribute_type')

                if not rew_type or not rew_amount:
                    continue

                reward = QuestReward(
                    quest_id=quest.id,
                    reward_type=rew_type,
                    amount=rew_amount,
                    item_id=int(rew_item) if rew_item and rew_item.isdigit() else None,
                    attribute_type=rew_attribute if rew_type == 'attribute' else None,
                    attribute_amount=rew_amount if rew_type == 'attribute' else None
                )
                db.session.add(reward)

            db.session.commit()

            update_quest_translations(quest, {
                'title': title,
                'description': description
            })

            flash("Quest added successfully!", 'success')
            return redirect(url_for('game.manage_quests'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding quest: {str(e)}", 'error')
            import logging
            logging.error(f"Quest creation error: {str(e)}")

    npcs = NPC.query.all()
    items = Item.query.all()
    factions = list(g.translations['game']['factions'].keys())

    return render_template('admin_add_quest.html',
                            translations=g.translations,
                            npcs=npcs,
                            items=items,
                            factions=factions)

def update_quest_translations(quest, form_data):
    try:
        lang = get_current_language()
        file_path = os.path.join(current_app.root_path, 'locales', lang, 'game', 'quests.json')
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        translations = {}
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                translations = json.load(f)
        
        translations[quest.translation_key] = {
            'title': form_data.get('title', ''),
            'description': form_data.get('description', '')
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to update quest translations: {str(e)}")
        raise

@game_bp.route('/admin/quests/edit/<int:quest_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_quest(quest_id):
    quest = Quest.query.get_or_404(quest_id)
    npcs = NPC.query.all()
    items = Item.query.all()
    factions = list(g.translations['game']['factions'].keys())

    if request.method == 'POST':
        try:
            quest.is_active = 'is_active' in request.form
            quest.is_unique = 'is_unique' in request.form
            quest.spawn_chance = float(request.form.get('spawn_chance'))

            old_translation_key = quest.translation_key
            new_translation_key = request.form.get('translation_key', old_translation_key)
            if new_translation_key != old_translation_key:
                remove_quest_translation(old_translation_key)
                quest.translation_key = new_translation_key

            update_quest_translations(quest, {
                'title': request.form['title'],
                'description': request.form['description']
            })

            QuestObjective.query.filter_by(quest_id=quest.id).delete()
            QuestReward.query.filter_by(quest_id=quest.id).delete()
            db.session.flush()

            objectives_data = {}
            for key, value in request.form.items():
                if key.startswith('objectives[') and key.endswith('][type]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['type'] = value
                elif key.startswith('objectives[') and key.endswith('][target]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['target'] = value
                elif key.startswith('objectives[') and key.endswith('][amount]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    objectives_data.setdefault(index, {})['amount'] = value

            for index in sorted(objectives_data.keys()):
                obj_data = objectives_data[index]
                obj_type = obj_data.get('type')
                obj_target = obj_data.get('target')
                obj_amount = obj_data.get('amount')

                if not obj_type or not obj_amount:
                    continue

                objective = QuestObjective(
                    quest_id=quest.id,
                    objective_type=obj_type,
                    target_value=obj_target if obj_target else None,
                    amount_required=int(obj_amount)
                )
                db.session.add(objective)

            rewards_data = {}
            for key, value in request.form.items():
                if key.startswith('rewards[') and key.endswith('][type]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['type'] = value
                elif key.startswith('rewards[') and key.endswith('][amount]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['amount'] = value
                elif key.startswith('rewards[') and key.endswith('][item]'):
                    index_str = key.split('[')[1].split(']')[0]
                    index = int(index_str)
                    rewards_data.setdefault(index, {})['item'] = value

            for index in sorted(rewards_data.keys()):
                rew_data = rewards_data[index]
                rew_type = rew_data.get('type')
                rew_amount = rew_data.get('amount')
                rew_item = rew_data.get('item')

                if not rew_type or not rew_amount:
                    continue

                reward = QuestReward(
                    quest_id=quest.id,
                    reward_type=rew_type,
                    amount=int(rew_amount),
                    item_id=int(rew_item) if rew_item and rew_item.isdigit() else None
                )
                db.session.add(reward)

            db.session.commit()
            flash("Quest updated successfully!", 'success')
            return redirect(url_for('game.manage_quests'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating quest: {str(e)}", 'error')
            current_app.logger.error(f"Quest update error: {str(e)}")

    return render_template('admin_edit_quest.html',
                            translations=g.translations,
                            quest=quest,
                            npcs=npcs,
                            items=items,
                            factions=factions)

    '''return render_template('admin_edit_quest.html',
                         translations=g.translations,
                         quest=quest,
                         npcs=npcs,
                         items=items,
                         factions=factions)'''

@game_bp.route('/admin/quests/delete/<int:quest_id>', methods=['POST'])
@login_required
@admin_required
def delete_quest(quest_id):
    quest = Quest.query.get_or_404(quest_id)
    
    QuestObjective.query.filter_by(quest_id=quest.id).delete()
    QuestReward.query.filter_by(quest_id=quest.id).delete()
    PlayerQuest.query.filter_by(quest_id=quest.id).delete()
    
    db.session.delete(quest)
    db.session.commit()
    
    remove_quest_translation(quest.translation_key)
    
    flash("Quest deleted successfully!", 'success')
    return redirect(url_for('game.manage_quests'))

def remove_quest_translation(translation_key):
    """Remove a quest from the translations file"""
    try:
        lang = get_current_language()
        file_path = os.path.join(current_app.root_path, 'locales', lang, 'game', 'quests.json')
        
        if not os.path.exists(file_path):
            return
            
        with open(file_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        
        translations.pop(translation_key, None)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to remove quest translation: {str(e)}")


@game_bp.route('/weaponshop')
@login_required
@not_jailed
def weapon_shop():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    current_lang = get_current_language()
    translations = load_translations(current_lang)
    
    weapons = Item.query.filter_by(item_type='weapon', is_npc_only=False).all()
    
    for weapon in weapons:
        print(f"Weapon: {weapon.translation_key}, Exists in translations: {weapon.translation_key in translations.get('game', {}).get('items', {}).get('weapon', {})}")
    
    equipped_items = {item.item_id for item in current_user.character.items if item.equipped}
    
    return render_template('weapon_shop.html',
                         translations=translations,
                         weapons=weapons,
                         equipped_items=equipped_items,
                         character=current_user.character)

@game_bp.route('/armorshop')
@login_required
@not_jailed
def armor_shop():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    current_lang = get_current_language()
    translations = load_translations(current_lang)
    
    if 'game' not in g.translations:
        g.translations['game'] = {}
    if 'items' not in g.translations['game']:
        g.translations['game']['items'] = {'armor': {
            'head': {}, 'body': {}, 'gloves': {}, 'pants': {}, 'boots': {}
        }}
    
    armor = Item.query.filter_by(item_type='armor', is_npc_only=False).all()
    
    equipped_items = {item.item_id for item in current_user.character.items if item.equipped}
    
    return render_template('armor_shop.html',
                         translations=translations,
                         armor=armor,
                         equipped_items=equipped_items,
                         character=current_user.character)

@game_bp.route('/accept-rules', methods=['POST'])
@login_required
def accept_rules():
    return '', 204

@game_bp.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    try:
        current_app.logger.info(f"Attempting to delete account for user {current_user.id}")
        
        character = current_user.character
        
        if character:
            current_app.logger.info(f"Deleting character {character.id} and associated data")
            
            Message.query.filter(
                (Message.sender_id == character.id) | 
                (Message.recipient_id == character.id)
            ).delete(synchronize_session=False)
            
            BattleLog.query.filter(
                (BattleLog.attacker_id == character.id) | 
                (BattleLog.defender_id == character.id) |
                (BattleLog.winner_id == character.id)
            ).delete(synchronize_session=False)
            
            CharacterItem.query.filter_by(character_id=character.id).delete()
            
            LotteryEntry.query.filter_by(character_id=character.id).delete()
            
            if character.is_jailed:
                Jail.query.filter_by(character_id=character.id).delete()
            
            db.session.delete(character)
        
        db.session.delete(current_user)
        db.session.commit()
        current_app.logger.info(f"Successfully deleted user {current_user.id}")
        
        logout_user()
        flash("Your account has been successfully deleted.", 'success')
        return redirect(url_for('auth.signup'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to delete account: {str(e)}", exc_info=True)
        flash("Failed to delete account. Please try again.", 'error')
        return redirect(url_for('game.dashboard'))


@game_bp.route('/admin/warning-users')
@login_required
@admin_required
def warning_users():
    duplicate_ips = get_duplicate_ips()
    
    return render_template('warning_users.html',
                         translations=g.translations,
                         duplicate_ips=duplicate_ips,
                         warning_count=len(duplicate_ips))

@game_bp.route('/admin/delete-duplicates/<ip_address>', methods=['POST'])
@login_required
@admin_required
def delete_duplicate_accounts(ip_address):
    try:
        users = User.query.filter_by(ip_address=ip_address)\
                         .order_by(User.created_at.asc()).all()
        
        if len(users) > 1:
            for user in users[1:]:
                db.session.delete(user)
            db.session.commit()
            flash(f"Deleted {len(users)-1} duplicate accounts for IP {ip_address}", 'success')
        else:
            flash("No duplicates found for this IP", 'info')
            
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting duplicates: {str(e)}", 'error')
    
    return redirect(url_for('game.warning_users'))

def get_duplicate_ips():
    duplicate_ips = {}
    ip_counts = db.session.query(
        User.ip_address,
        db.func.count(User.ip_address).label('count')
    ).group_by(User.ip_address).all()
    
    for ip, count in ip_counts:
        if count > 1 and ip:  # Skip null IPs
            users = User.query.filter_by(ip_address=ip)\
                            .order_by(User.created_at.asc()).all()
            duplicate_ips[ip] = users
    
    return duplicate_ips

@game_bp.route('/admin/users/add-item/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_add_item(user_id):
    user = User.query.get_or_404(user_id)
    item_id = request.form.get('item_id')
    
    if not item_id:
        flash("Please select an item", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    item = Item.query.get(item_id)
    if not item:
        flash("Invalid item selected", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    character_item = CharacterItem(
        character_id=user.character.id,
        item_id=item.id,
        equipped=False
    )
    
    db.session.add(character_item)
    db.session.commit()
    
    flash(f"Added {item.translation_key} to {user.username}'s inventory", 'success')
    return redirect(url_for('game.edit_user', user_id=user_id))


@game_bp.route('/admin/users/revive/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_revive(user_id):
    user = User.query.get_or_404(user_id)
    if not user.character:
        flash("User has no character", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    user.character.healthpoints = user.character.max_healthpoints
    user.character.is_dead = False
    db.session.commit()
    
    flash("Character revived to full health", 'success')
    return redirect(url_for('game.edit_user', user_id=user_id))

@game_bp.route('/admin/users/heal/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_heal(user_id):
    user = User.query.get_or_404(user_id)
    if not user.character:
        flash("User has no character", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    user.character.healthpoints = user.character.max_healthpoints
    db.session.commit()
    
    flash("Character fully healed", 'success')
    return redirect(url_for('game.edit_user', user_id=user_id))

@game_bp.route('/admin/users/refresh-resources/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_refresh_resources(user_id):
    user = User.query.get_or_404(user_id)
    if not user.character:
        flash("User has no character", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    user.character.refresh_resources()
    db.session.commit()
    
    flash("Character resources refreshed", 'success')
    return redirect(url_for('game.edit_user', user_id=user_id))

@game_bp.route('/admin/users/remove-item/<int:user_id>/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def admin_remove_item(user_id, item_id):
    user = User.query.get_or_404(user_id)
    if not user.character:
        flash("User has no character", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    character_item = CharacterItem.query.filter_by(
        character_id=user.character.id,
        item_id=item_id
    ).first()
    
    if not character_item:
        flash("Item not found in character's inventory", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    db.session.delete(character_item)
    db.session.commit()
    
    flash("Item removed from inventory", 'success')
    return redirect(url_for('game.edit_user', user_id=user_id))
    
@game_bp.route('/admin/users/give-gold/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def admin_give_gold(user_id):
    user = User.query.get_or_404(user_id)
    if not user.character:
        flash("User has no character", 'error')
        return redirect(url_for('game.edit_user', user_id=user_id))
    
    try:
        amount = int(request.form.get('amount', 0))
        if amount <= 0:
            flash("Amount must be positive", 'error')
            return redirect(url_for('game.edit_user', user_id=user_id))
        
        user.character.gold += amount
        db.session.commit()
        
        flash(f"Added {amount} gold to {user.username}'s character", 'success')
    except ValueError:
        flash("Invalid amount", 'error')
    
    return redirect(url_for('game.edit_user', user_id=user_id))
    
@game_bp.route('/update-motto', methods=['POST'])
@login_required
@not_jailed
def update_motto():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    motto = request.form.get('motto', '')[:200]
    current_user.character.motto = motto
    db.session.commit()
    
    flash("Motto updated!", 'success')
    return redirect(url_for('game.view_character', character_id=current_user.character.id))

@game_bp.route('/academy', methods=['GET', 'POST'])
@login_required
@not_jailed
def academy():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    
    if character.is_dead:
        flash("g.translations['game']['academy']['dead']", 'error')
        return redirect(url_for('game.dashboard'))
    
    character.update_resources()
    db.session.commit()
    
    faction_data = g.translations['game']['factions'].get(character.faction, {})
    resource_name = faction_data.get('stats', {}).get('resource', 'Recursos')
    resources_to_use = (character.resource // 5) * 5
    training_results = []
    total_change = 0
    
    if request.method == 'POST':
        if character.is_dead:
            flash("g.translations['game']['academy']['dead']", 'error')
            return redirect(url_for('game.dashboard'))
            
        attribute = request.form.get('attribute')
        amount_option = request.form.get('amount')
        
        if not attribute or not amount_option:
            flash("Invalid training request", 'error')
            return redirect(url_for('game.academy'))
        
        if amount_option == '5':
            resources_to_use = 5
        elif amount_option == '10':
            resources_to_use = 10
        elif amount_option == '50':
            resources_to_use = 50
        elif amount_option == 'all':
            resources_to_use = (character.resource // 5) * 5
        else:
            flash("Invalid amount option", 'error')
            return redirect(url_for('game.academy'))
        
        if resources_to_use == 0 or character.resource < resources_to_use:
            flash(f"{g.translations['game']['academy']['without']} {resource_name.lower()} {g.translations['game']['academy']['to_train']}", 'error')
            return redirect(url_for('game.academy'))
        
        training_sessions = resources_to_use // 5
        
        for session in range(training_sessions):
            if random.random() < 0.05:
                change = -random.uniform(0.001, 1.499)
                result_type = 'loss'
                quality = None
            else:
                rand = random.random()
                if rand < 0.90:  # 80% chance
                    change = random.uniform(0.001, 0.400)
                    quality = 'normal'
                elif rand < 0.95:  # 15% chance (0.80-0.95)
                    change = random.uniform(0.401, 0.600)
                    quality = 'good'
                else:  # 5% chance (0.95-1.00)
                    change = random.uniform(0.601, 1.000)
                    quality = 'excellent'
                result_type = 'gain'
            
            total_change += change
            
            training_results.append({
                'resources_used': 5,
                'change': change,
                'type': result_type,
                'attribute': attribute,
                'quality': quality
            })
            
            if attribute == 'destreza':
                character.destreza = max(0, character.destreza + change)
            elif attribute == 'forca':
                character.forca = max(0, character.forca + change)
            elif attribute == 'inteligencia':
                character.inteligencia = max(0, character.inteligencia + change)
            elif attribute == 'devocao':
                character.devocao = max(0, character.devocao + change)
                
            check_and_update_quests(
                character, 
                'train_attribute', 
                attribute_trained=attribute,
                attribute_amount=abs(change)
            )
        
        character.resource -= resources_to_use
        
       
        flash(f"{g.translations['game']['academy']['training_completed']} {resources_to_use} {resource_name.lower()}", 'success')
        db.session.commit()
    
    return render_template('academy.html',
                         translations=g.translations,
                         character=character,
                         training_results=training_results,
                         total_change=total_change,
                         resource_name=resource_name,
                         resources_to_use=resources_to_use)
                         
def get_enemy_faction(faction):
    enemy_factions = {
        'veylan': 'urghan',
        'urghan': 'aureen',
        'aureen': 'camyra',
        'camyra': 'veylan'
    }
    return enemy_factions.get(faction.lower())
    
def get_min_attackable_level(attacker_level):
    """Calculate the minimum level a player can attack based on their level"""
    if attacker_level <= 15:
        return 1
    return max(1, int(attacker_level / 1.5))

@game_bp.route('/market')
@login_required
@not_jailed
def market():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    return render_template('market.html', translations=g.translations)  
    
@game_bp.route('/fights')
@login_required
@not_jailed
def fights():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    return render_template('battles.html', translations=g.translations)  

@game_bp.route('/arena', methods=['GET', 'POST'])
@login_required
@not_jailed
def arena():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    if current_user.character.is_dead:
        flash("You need to revive yourself before entering the arena!", 'error')
        return redirect(url_for('game.dashboard'))

    min_level = 1
    max_level = None  # No max level by default
    
    if request.method == 'POST':
        try:
            min_level = max(1, int(request.form.get('min_level', 1)))
            
            max_level_str = request.form.get('max_level', '').strip()
            max_level = int(max_level_str) if max_level_str else None
            
            if max_level is not None:
                max_level = max(1, max_level)
                if min_level > max_level:
                    min_level, max_level = max_level, min_level
                    flash("Minimum level was higher than maximum - values have been swapped", 'info')
                    
        except ValueError:
            flash("Invalid level input - using default values", 'error')
            min_level = 1
            max_level = None
    
    query = Character.query.filter(
        Character.id != current_user.character.id,
        Character.is_dead == False,
        Character.level >= min_level,
        Character.level >= get_min_attackable_level(current_user.character.level)
    )
    
    if max_level is not None:
        query = query.filter(Character.level <= max_level)
    
    page = request.args.get('page', 1, type=int)
    opponents = query.order_by(Character.level.desc()).paginate(page=page, per_page=20)
    
    return render_template('arena.html',
                         translations=g.translations,
                         opponents=opponents,
                         min_level=min_level,
                         max_level=max_level,
                         current_level=current_user.character.level,
                         min_attackable_level=get_min_attackable_level(current_user.character.level))

@game_bp.route('/online')
@login_required
@not_jailed
def online_players():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    online_users = User.query.filter(
        User.last_activity > datetime.utcnow() - timedelta(minutes=5),
        User.character != None
    ).join(Character).order_by(Character.level.desc()).all()
    
    return render_template('online.html',
                         translations=g.translations,
                         online_players=online_users)

@game_bp.route('/bank', methods=['GET', 'POST'])
@login_required
@not_jailed
def bank():
    character = current_user.character
    
    if request.method == 'POST':
        action = request.form.get('action')
        amount_str = request.form.get('amount')

        if action == 'deposit':
            if amount_str == 'all':
                amount = character.gold
            else:
                try:
                    amount = int(amount_str)
                except ValueError:
                    flash(g.translations['game']['bank']['invalid_amount'], 'error')
                    return redirect(url_for('game.bank'))

            if amount <= 0:
                flash(g.translations['game']['bank']['positive_amount'], 'error')
                return redirect(url_for('game.bank'))

            if character.gold >= amount:
                character.gold -= amount
                character.bank_gold += amount
                db.session.commit()
                flash(g.translations['game']['bank']['deposit_success'].format(amount=amount), 'success')
                check_and_update_quests(character, 'deposit_gold', amount=amount)
                db.session.commit()
            else:
                flash(g.translations['game']['bank']['not_enough_gold_on_hand'], 'error')

        elif action == 'withdraw':
            if amount_str == 'all':
                amount = character.bank_gold
            else:
                try:
                    amount = int(amount_str)
                except ValueError:
                    flash(g.translations['game']['bank']['invalid_amount'], 'error')
                    return redirect(url_for('game.bank'))

            if amount <= 0:
                flash(g.translations['game']['bank']['positive_amount'], 'error')
                return redirect(url_for('game.bank'))

            if character.bank_gold >= amount:
                character.bank_gold -= amount
                character.gold += amount
                db.session.commit()
                flash(g.translations['game']['bank']['withdraw_success'].format(amount=amount), 'success')
                check_and_update_quests(character, 'withdraw_gold', amount=amount)
                db.session.commit()
            else:
                flash(g.translations['game']['bank']['not_enough_gold_in_bank'], 'error')

        return redirect(url_for('game.bank'))
    
    return render_template('bank.html', translations=g.translations)

@game_bp.route('/bank/deposit', methods=['POST'])
@login_required
@not_jailed
def bank_deposit():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    
    try:
        if 'deposit_all' in request.form:
            amount = character.gold
        else:
            amount = int(request.form['amount'])
            amount = min(amount, character.gold)
        
        if amount <= 0:
            flash("Invalid amount", 'error')
            return redirect(url_for('game.bank'))
        
        character.gold -= amount
        character.bank_gold += amount
        db.session.commit()
        
        check_and_update_quests(character, 'deposit_gold', amount=amount)
        db.session.commit()
        
        flash(f"Deposited {amount} gold to bank", 'success')
    except (ValueError, KeyError):
        flash("Invalid deposit amount", 'error')
    
    return redirect(url_for('game.bank'))

@game_bp.route('/bank/withdraw', methods=['POST'])
@login_required
@not_jailed
def bank_withdraw():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    
    try:
        if 'withdraw_all' in request.form:
            amount = character.bank_gold
        else:
            amount = int(request.form['amount'])
            amount = min(amount, character.bank_gold)
        
        if amount <= 0:
            flash("Invalid amount", 'error')
            return redirect(url_for('game.bank'))
        
        character.bank_gold -= amount
        character.gold += amount
        db.session.commit()
        
        check_and_update_quests(character, 'withdraw_gold', amount=amount)
        db.session.commit()
        
        flash(f"Withdrew {amount} gold from bank", 'success')
    except (ValueError, KeyError):
        flash("Invalid withdrawal amount", 'error')
    
    return redirect(url_for('game.bank'))

@game_bp.route('/mine', methods=['GET', 'POST'])
@login_required
@not_jailed
def mine():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))

    import math
    import random
    from datetime import datetime, timedelta

    character = current_user.character
    character.update_resources()

    if request.method == 'POST':
        try:
            amount_option = request.form.get('amount')

            if amount_option == '1':
                resources_to_use = 1
            elif amount_option == '5':
                resources_to_use = 5
            elif amount_option == '50':
                resources_to_use = 50
            elif amount_option == 'all':
                resources_to_use = character.resource
            else:
                flash("Invalid amount option", 'error')
                return redirect(url_for('game.mine'))

            if resources_to_use <= 0 or character.resource < resources_to_use:
                flash("Not enough resources to mine", 'error')
                return redirect(url_for('game.mine'))

            mining_results = []
            total_gold = 0
            total_diamonds = 0
            total_mining_level = 0

            lottery = MiningLottery.query.first()
            if not lottery:
                lottery = MiningLottery(current_gold=0, current_diamonds=0)
                db.session.add(lottery)

            # --- DAILY STREAK BONUS ---
            streak_bonus = 0.0
            today = datetime.utcnow().date()

            if character.last_mine_date:
                last_date = character.last_mine_date.date()
                if last_date == today - timedelta(days=1):
                    character.mining_streak += 1
                elif last_date == today:
                    pass
                else:
                    character.mining_streak = 1
            else:
                character.mining_streak = 1

            character.last_mine_date = datetime.utcnow()
            streak_bonus = min(character.mining_streak * 0.02, 0.14)  # Max 14%

            current_mining_level = character.mining_level

            for _ in range(resources_to_use):
                mining_gain = random.randint(1, 30) if random.random() < 0.95 else random.randint(30, 100)
                current_mining_level += mining_gain
                total_mining_level += mining_gain

                base_gold = int(10 + math.log(current_mining_level + 1, 1.5))

                milestone_levels = [1000 * (2 ** i) for i in range(20)]
                bonus_multiplier = 1.0
                for level in milestone_levels:
                    if current_mining_level >= level:
                        bonus_multiplier *= 1.1
                    else:
                        break

                random_factor = random.uniform(0.9, 1.1)

                crit_multiplier = 2.0 if random.random() < 0.05 else 1.0

                broke_tool = random.random() < 0.02

                gold_gain = int(base_gold * bonus_multiplier * random_factor * (1 + streak_bonus) * crit_multiplier)
                if gold_gain > 200:
                    gold_gain = 200

                final_gold_gain = 0 if broke_tool else gold_gain

                base_chance = 0.005 + min(current_mining_level, 50000) / 200000  # Starts at 0.5%, max 25% at 50k
                extra_levels = max(current_mining_level - 50000, 0)
                extra_bonus = (extra_levels // 50000) * 0.025  # +2.5% per 50k over
                diamond_chance = min(base_chance + extra_bonus, 0.35)
                diamonds_gain = 1 if random.random() < diamond_chance else 0

                mining_results.append({
                    'mining_gain': mining_gain,
                    'gold_gain': final_gold_gain,
                    'diamonds_gain': diamonds_gain,
                    'crit': crit_multiplier == 2.0,
                    'broke_tool': broke_tool,
                    'current_mining_level': current_mining_level
                })

                total_gold += final_gold_gain
                total_diamonds += diamonds_gain

            owner_gold = total_gold // 10
            
            if 3 <= total_diamonds <= 9:
                owner_diamonds = 1
            else:
                owner_diamonds = total_diamonds // 10

            player_gold = total_gold - owner_gold
            player_diamonds = total_diamonds - owner_diamonds

            character.mining_level = current_mining_level
            character.gold += player_gold
            character.diamonds += player_diamonds
            character.resource -= resources_to_use

            lottery.current_gold += owner_gold
            lottery.current_diamonds += owner_diamonds

            db.session.commit()

            return render_template('mining_result.html',
                                   translations=g.translations,
                                   mining_results=mining_results,
                                   total_gold=player_gold,
                                   total_diamonds=player_diamonds,
                                   total_mining_level=total_mining_level,
                                   resources_used=resources_to_use,
                                   streak_bonus=int(streak_bonus * 100),
                                   owner_gold=owner_gold,
                                   owner_diamonds=owner_diamonds)

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Mining error: {str(e)}", exc_info=True)
            flash("An error occurred while mining", 'error')
            return redirect(url_for('game.mine'))

    return render_template('mine.html',
                           translations=g.translations,
                           character=current_user.character)

@game_bp.route('/lottery')
@login_required
@not_jailed
def lottery():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    lottery = MiningLottery.query.first()
    if not lottery:
        lottery = MiningLottery()
        db.session.add(lottery)
        db.session.commit()
    
    entries_count = LotteryEntry.query.count()
    user_entries = LotteryEntry.query.filter_by(character_id=current_user.character.id).count()
    
    next_entry_cost = 1000 * (user_entries + 1)
    
    last_winners = LotteryWinner.query.order_by(LotteryWinner.win_time.desc()).limit(5).all()
    
    return render_template('lottery.html',
                         translations=g.translations,
                         lottery=lottery,
                         entries_count=entries_count,
                         last_winners=last_winners,
                         user_entries=user_entries,
                         next_entry_cost=next_entry_cost,
                         character=current_user.character)

@game_bp.route('/lottery/enter', methods=['POST'])
@login_required
@not_jailed
def enter_lottery():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    
    if character.mining_level < 5000:
        flash("You need Mining Level 5000 to enter the lottery!", 'error')
        return redirect(url_for('game.lottery'))
    
    user_entries = LotteryEntry.query.filter_by(character_id=character.id).count()
    cost = 1000 * (user_entries + 1)
    
    if character.gold < cost:
        flash(f"You need {cost} gold to enter the lottery!", 'error')
        return redirect(url_for('game.lottery'))
    
    character.gold -= cost
    
    lottery = MiningLottery.query.first()
    if not lottery:
        lottery = MiningLottery()
        db.session.add(lottery)
    lottery.current_gold += cost
    
    entry = LotteryEntry(character_id=character.id, cost=cost)
    db.session.add(entry)
    db.session.commit()
    
    flash(f"You've successfully entered the mining lottery for {cost} gold!", 'success')
    return redirect(url_for('game.lottery'))

@game_bp.route('/lottery/draw', methods=['POST'])
@login_required
@admin_required
def draw_lottery():
    lottery = MiningLottery.query.first()
    if not lottery:
        lottery = MiningLottery()
        db.session.add(lottery)
        db.session.commit()
    
    entries = LotteryEntry.query.all()
    if not entries:
        flash("No entries in the lottery yet!", 'error')
        return redirect(url_for('game.lottery'))
    
    winner_entry = random.choice(entries)
    winner = winner_entry.character
    
    winner.gold += lottery.current_gold
    winner.diamonds += lottery.current_diamonds
    
    lottery_winner = LotteryWinner(
        character_id=winner.id,
        gold_won=lottery.current_gold,
        diamonds_won=lottery.current_diamonds
    )
    db.session.add(lottery_winner)
    
    lottery.current_gold = 0
    lottery.current_diamonds = 20
    lottery.last_draw_time = datetime.utcnow()
    
    LotteryEntry.query.delete()
    
    db.session.commit()
    
    flash(f"Lottery drawn! Winner: {winner.name} - {lottery.current_gold} gold and {lottery.current_diamonds} diamonds!", 'success')
    return redirect(url_for('game.lottery'))
    
@game_bp.route('/mailbox')
@login_required
def mailbox():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    messages = Message.query.filter(
        Message.recipient_id == current_user.character.id,
        Message.expires_at > datetime.utcnow()
    ).order_by(Message.is_read, Message.created_at.desc()).all()
    
    return render_template('mailbox.html',
                         translations=g.translations,
                         messages=messages)

@game_bp.route('/message/<int:message_id>')
@login_required
def view_message(message_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    message = Message.query.get_or_404(message_id)
    
    if not (current_user.is_admin or message.recipient_id == current_user.character.id):
        flash("You can only view your own messages", 'error')
        return redirect(url_for('game.mailbox'))
    
    if not message.is_read and message.recipient_id == current_user.character.id:
        message.is_read = True
        db.session.commit()
    
    replies = Message.query.filter_by(parent_message_id=message_id).order_by(Message.created_at).all()
    
    return render_template('view_message.html',
                         translations=g.translations,
                         message=message,
                         replies=replies)

@game_bp.route('/message/send', methods=['GET', 'POST'])
@login_required
def send_message():
    if not current_user.character:
        return redirect(url_for('game.create_character'))

    recipient_id = None
    parent_message_id = None
    parent_message = None
    is_jailed_user = current_user.character.is_jailed if hasattr(current_user, 'character') else False

    if request.method == 'POST':
        recipient_id = request.form.get('recipient_id', type=int)
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        parent_message_id = request.form.get('parent_message_id', type=int)
        
        if is_jailed_user:
            if not recipient_id:
                flash("As a jailed player, you can only message admins", 'error')
                return redirect(url_for('game.jail'))
            
            recipient = Character.query.get(recipient_id)
            if not recipient or not recipient.user.is_admin:
                flash("As a jailed player, you can only message admins", 'error')
                return redirect(url_for('game.jail'))
        
        if not recipient_id:
            flash("Recipient is required", 'error')
            return redirect(url_for('game.send_message'))
        
        if not subject or not body:
            flash("Subject and body are required", 'error')
            return redirect(url_for('game.send_message'))
        
        recipient = Character.query.get(recipient_id)
        if not recipient:
            flash("Recipient not found", 'error')
            return redirect(url_for('game.send_message'))
        
        message = Message(
            sender_id=current_user.character.id,
            recipient_id=recipient_id,
            subject=subject,
            body=body,
            parent_message_id=parent_message_id
        )
        
        db.session.add(message)
        db.session.commit()
        
        flash("Message sent successfully!", 'success')
        return redirect(url_for('game.mailbox'))
    
    recipient_id = request.args.get('recipient_id', type=int)
    parent_message_id = request.args.get('parent_message_id', type=int)
    
    if is_jailed_user:
        if not recipient_id:
            admin = User.query.filter_by(is_admin=True).first()
            if admin and admin.character:
                recipient_id = admin.character.id
            else:
                flash("No admin available to contact", 'error')
                return redirect(url_for('game.jail'))
        
        recipient = Character.query.get(recipient_id)
        if not recipient or not recipient.user.is_admin:
            flash("As a jailed player, you can only message admins", 'error')
            return redirect(url_for('game.jail'))
    
    recipient = None
    if recipient_id:
        recipient = Character.query.get(recipient_id)
    
    if parent_message_id:
        parent_message = Message.query.get(parent_message_id)
    
    subject_prefix = ""
    if is_jailed_user:
        subject_prefix = "[JAIL APPEAL] "
    elif parent_message:
        subject_prefix = f"Re: {parent_message.subject}"
    
    return render_template('send_message.html',
                         translations=g.translations,
                         recipient=recipient,
                         subject_prefix=subject_prefix,
                         parent_message=parent_message)

@game_bp.route('/message/delete/<int:message_id>', methods=['POST'])
@login_required
def delete_message(message_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    message = Message.query.get_or_404(message_id)
    
    if message.recipient_id != current_user.character.id:
        flash("You can only delete your own messages", 'error')
        return redirect(url_for('game.mailbox'))
    
    db.session.delete(message)
    db.session.commit()
    
    flash("Message deleted", 'success')
    return redirect(url_for('game.mailbox'))

@game_bp.route('/admin/messages')
@login_required
@admin_required
def admin_messages():
    messages = Message.query.filter(
        Message.expires_at > datetime.utcnow()
    ).order_by(Message.created_at.desc()).all()
    
    reports = MessageReport.query.filter_by(resolved=False).order_by(MessageReport.created_at.desc()).all()
    
    return render_template('admin_messages.html',
                         translations=g.translations,
                         messages=messages,
                         reports=reports)

@game_bp.route('/admin/messages/send-to-all', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_send_to_all():
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        
        if not subject or not body:
            flash("Subject and body are required", 'error')
            return redirect(url_for('game.admin_send_to_all'))
        
        characters = Character.query.all()
        
        for character in characters:
            message = Message(
                sender_id=current_user.character.id,
                recipient_id=character.id,
                subject=subject,
                body=body,
                is_admin_message=True
            )
            db.session.add(message)
        
        db.session.commit()
        
        flash(f"Message sent to {len(characters)} players", 'success')
        return redirect(url_for('game.admin_messages'))
    
    return render_template('admin_send_to_all.html',
                         translations=g.translations)

@game_bp.route('/message/report/<int:message_id>', methods=['POST'])
@login_required
def report_message(message_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    message = Message.query.get_or_404(message_id)
    reason = request.form.get('reason', '').strip()
    
    if not reason:
        flash("Please provide a reason for reporting this message", 'error')
        return redirect(url_for('game.view_message', message_id=message_id))
    
    report = MessageReport(
        message_id=message.id,
        reporter_id=current_user.character.id,
        reason=reason
    )
    
    db.session.add(report)
    db.session.commit()
    
    flash("Message has been reported. Thank you for your feedback.", 'success')
    return redirect(url_for('game.view_message', message_id=message_id))

@game_bp.route('/admin/resolve-report/<int:report_id>', methods=['POST'])
@login_required
@admin_required
def resolve_report(report_id):
    report = MessageReport.query.get_or_404(report_id)
    report.resolved = True
    db.session.commit()
    flash("Report marked as resolved", 'success')
    return redirect(url_for('game.admin_messages'))

@game_bp.route('/admin/jail/<int:character_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def jail_character(character_id):
    character = Character.query.get_or_404(character_id)
    
    if request.method == 'POST':
        try:
            duration = int(request.form['duration'])
            duration_unit = request.form['duration_unit']
            real_reason = request.form['real_reason']
            game_reason = request.form['game_reason']
            
            if duration_unit == 'hours':
                total_minutes = duration * 60
            elif duration_unit == 'days':
                total_minutes = duration * 1440
            else:  # minutes
                total_minutes = duration
            
            jail = Jail(
                character_id=character.id,
                admin_id=current_user.character.id,
                duration=total_minutes,
                duration_unit=duration_unit,
                real_reason=real_reason,
                game_reason=game_reason
            )
            
            character.is_jailed = True
            character.current_jail = jail
            
            db.session.add(jail)
            db.session.commit()
            
            flash(f"{character.name} has been jailed successfully", 'success')
            return redirect(url_for('game.view_character', character_id=character.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error jailing character: {str(e)}", 'error')
    
    return render_template('jail_set.html', 
                         translations=g.translations,
                         character=character)

@game_bp.route('/jail')
@login_required
def jail():
    jailed_characters = Character.query.filter_by(is_jailed=True).all()
    
    if current_user.character.is_jailed:
        jail_record = current_user.character.current_jail
        return render_template('jail.html',
                            translations=g.translations,
                            jailed_characters=jailed_characters,
                            jail_record=jail_record,
                            is_jailed=True,
                            timedelta=timedelta,  # Pass timedelta to template
                            datetime=datetime)
    
    return render_template('jail.html',
                         translations=g.translations,
                         jailed_characters=jailed_characters,
                         is_jailed=False,
                         timedelta=timedelta,     # Pass timedelta to template
                         datetime=datetime) 

@game_bp.before_request
def check_jailed():
    if current_user.is_authenticated:
        if request.endpoint == 'game.create_character':
            return
        
        if not hasattr(current_user, 'character') or current_user.character is None:
            return redirect(url_for('game.create_character'))
        
        if current_user.character.is_jailed:
            jail_record = current_user.character.current_jail
            if datetime.utcnow() > jail_record.start_time + timedelta(minutes=jail_record.duration):
                current_user.character.is_jailed = False
                jail_record.is_released = True
                db.session.commit()
            elif request.endpoint not in ['game.jail', 'game.mailbox', 'game.view_message', 'game.send_message']:
                return redirect(url_for('game.jail'))

@game_bp.route('/admin/release-jail/<int:character_id>', methods=['POST'])
@login_required
@admin_required
def release_from_jail(character_id):
    character = Character.query.get_or_404(character_id)
    
    if not character.is_jailed:
        flash("This character is not jailed", 'error')
        return redirect(url_for('game.jail'))
    
    character.is_jailed = False
    character.current_jail.is_released = True
    db.session.commit()
    
    flash(f"{character.name} has been released from jail", 'success')
    return redirect(url_for('game.jail'))

@game_bp.route('/admin/npcs')
@login_required
@admin_required
def manage_npcs():
    npcs = NPC.query.order_by(NPC.level).all()
    return render_template('admin_npcs.html',
                         translations=g.translations,
                         npcs=npcs)

@game_bp.route('/admin/npcs/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_npc():
    if request.method == 'POST':
        try:
            translation_key = request.form['translation_key']
            level = int(request.form['level'])
            healthpoints = int(request.form['healthpoints'])
            max_healthpoints = int(request.form['max_healthpoints'])
            weapon_id = int(request.form['weapon_id']) if request.form['weapon_id'] else None
            armor_id = int(request.form['armor_id']) if request.form['armor_id'] else None
            min_xp = int(request.form['min_xp'])
            max_xp = int(request.form['max_xp'])
            min_gold = int(request.form['min_gold'])
            max_gold = int(request.form['max_gold'])
            image = request.form['image'] or 'npc.webp'
            reputation = int(request.form['reputation'])
            inteligencia = float(request.form['inteligencia'])
            destreza = float(request.form['destreza'])
            forca = float(request.form['forca'])
            devocao = float(request.form['devocao'])
            faction = request.form['faction']
            
            new_npc = NPC(
                translation_key=translation_key,
                level=level,
                healthpoints=healthpoints,
                max_healthpoints=max_healthpoints,
                weapon_id=weapon_id,
                armor_id=armor_id,
                min_xp=min_xp,
                max_xp=max_xp,
                min_gold=min_gold,
                max_gold=max_gold,
                image=image,
                reputation=reputation,
                inteligencia=inteligencia,
                destreza=destreza,
                forca=forca,
                devocao=devocao,
                faction=faction
            )
            
            db.session.add(new_npc)
            db.session.commit()
            
            update_npc_translations(new_npc)
            flash(g.translations['admin']['npc_added'], 'success')
            return redirect(url_for('game.manage_npcs'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding NPC: {str(e)}", 'error')
    
    weapons = Item.query.filter_by(item_type='weapon').all()
    armors = Item.query.filter_by(item_type='armor').all()
    factions = list(g.translations['game']['factions'].keys())
    
    return render_template('admin_add_npc.html',
                         translations=g.translations,
                         weapons=weapons,
                         armors=armors,
                         factions=factions)

@game_bp.route('/admin/npcs/edit/<int:npc_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_npc(npc_id):
    npc = NPC.query.get_or_404(npc_id)
    
    if request.method == 'POST':
        try:
            old_translation_key = npc.translation_key
            npc.translation_key = request.form['translation_key']
            npc.level = int(request.form['level'])
            npc.healthpoints = int(request.form['healthpoints'])
            npc.max_healthpoints = int(request.form['max_healthpoints'])
            npc.weapon_id = int(request.form['weapon_id']) if request.form['weapon_id'] else None
            npc.armor_id = int(request.form['armor_id']) if request.form['armor_id'] else None
            npc.min_xp = int(request.form['min_xp'])
            npc.max_xp = int(request.form['max_xp'])
            npc.min_gold = int(request.form['min_gold'])
            npc.max_gold = int(request.form['max_gold'])
            npc.image = request.form['image'] or 'npc.webp'
            npc.reputation = int(request.form['reputation'])
            npc.inteligencia = float(request.form['inteligencia'])
            npc.destreza = float(request.form['destreza'])
            npc.forca = float(request.form['forca'])
            npc.devocao = float(request.form['devocao'])
            npc.faction = request.form['faction']
            
            db.session.commit()
            
            if old_translation_key != npc.translation_key:
                update_npc_translations(npc)
                remove_npc_translation(old_translation_key)
            
            flash(g.translations['admin']['npc_updated'], 'success')
            return redirect(url_for('game.manage_npcs'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating NPC: {str(e)}", 'error')
    
    weapons = Item.query.filter_by(item_type='weapon').all()
    armors = Item.query.filter_by(item_type='armor').all()
    factions = list(g.translations['game']['factions'].keys())
    
    return render_template('admin_edit_npc.html',
                         translations=g.translations,
                         npc=npc,
                         weapons=weapons,
                         armors=armors,
                         factions=factions)

@game_bp.route('/admin/npcs/delete/<int:npc_id>', methods=['POST'])
@login_required
@admin_required
def delete_npc(npc_id):
    npc = NPC.query.get_or_404(npc_id)
    db.session.delete(npc)
    db.session.commit()
    flash(g.translations['admin']['npc_deleted'], 'success')
    return redirect(url_for('game.manage_npcs'))

def update_npc_translations(npc):
    """Update the translations JSON file with new NPC data"""
    try:
        lang = get_current_language()
        file_path = os.path.join(current_app.root_path, 'locales', lang, 'game', 'npcs.json')
        
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        translations = {}
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                translations = json.load(f)
        
        friendly_name = npc.translation_key.replace('_', ' ').title()
        
        translations[npc.translation_key] = {
            'name': friendly_name,
            'description': f"A {friendly_name.lower()}",
            'motto': f"I am {friendly_name.lower()}"
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to update NPC translations: {str(e)}")
        raise

def remove_npc_translation(translation_key):
    """Remove an NPC from the translations file"""
    try:
        lang = get_current_language()
        file_path = os.path.join(current_app.root_path, 'locales', lang, 'game', 'npcs.json')
        
        if not os.path.exists(file_path):
            return
            
        with open(file_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        
        translations.pop(translation_key, None)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to remove NPC translation: {str(e)}")

@game_bp.route('/battlefield')
@login_required
@not_jailed
def battlefield():
    npcs = NPC.query.order_by(NPC.level).all()
    return render_template('battlefield.html',
                         translations=g.translations,
                         npcs=npcs)

@game_bp.route('/npc/<int:npc_id>')
@login_required
@not_jailed
def view_npc(npc_id):
    npc = NPC.query.get_or_404(npc_id)
    faction_data = g.translations['game']['factions'].get(npc.faction, {})
    return render_template('npc_view_page.html',
                         npc=npc,
                         translations=g.translations,
                         faction_data=faction_data)

@game_bp.route('/fight-npc/<int:npc_id>', methods=['GET', 'POST'])
@login_required
@not_jailed
def fight_npc(npc_id):
    if not current_user.character:
        flash("You need a character to fight!", 'error')
        return redirect(url_for('game.dashboard'))
    
    if current_user.character.resource < 1:
        flash("You need at least 1 resource to attack!", 'error')
        return redirect(url_for('game.view_npc', npc_id=npc_id))
    
    current_user.character.resource -= 1
    
    npc = NPC.query.get_or_404(npc_id)
    attacker = current_user.character
    
    if attacker.is_dead:
        flash("You're dead! You need to heal before fighting.", 'error')
        return redirect(url_for('game.dashboard'))
    
    fight_log = []
    
    attacker_initial_rep = attacker.reputation
    
    def calculate_defense(character):
        defense = 0
        if isinstance(character, NPC):
            if character.armor and 'defense' in character.armor.stats:
                defense = character.armor.stats['defense']
        else:
            for item in character.items:
                if item.equipped and item.item.item_type == 'armor' and 'defense' in item.item.stats:
                    defense += item.item.stats['defense']
        return defense
    
    attacker_defense = calculate_defense(attacker)
    npc_defense = calculate_defense(npc)
    
    attacker_first = True
    
    while True:
        damage, weapon_name = calculate_damage(attacker, npc)
        npc.healthpoints = max(0, npc.healthpoints - damage)
        fight_log.append(Markup(f"{attacker.name} hits {g.translations['game']['npcs'][npc.translation_key]['name']} with {weapon_name} for <span class='damage'>{damage} damage</span>!"))
        
        if npc.healthpoints <= 0:
            npc.healthpoints = 0
            fight_log.append(f"{attacker.name} has defeated {g.translations['game']['npcs'][npc.translation_key]['name']}!")
            winner = "player"
            break
        
        damage, weapon_name = calculate_damage(npc, attacker)  # Simplified NPC damage
        attacker.healthpoints = max(0, attacker.healthpoints - damage)
        fight_log.append(Markup(f"{g.translations['game']['npcs'][npc.translation_key]['name']} hits {attacker.name} for <span class='damage'>{damage} damage</span>!"))
        
        if attacker.healthpoints <= 0:
            attacker.healthpoints = 0
            attacker.is_dead = True
            fight_log.append(f"{g.translations['game']['npcs'][npc.translation_key]['name']} has defeated {attacker.name}!")
            winner = "npc"
            break
    
    heal_message = None
    if not attacker.is_dead and attacker.healthpoints < attacker.max_healthpoints:
        hp_needed = attacker.max_healthpoints - attacker.healthpoints
        gold_available = attacker.gold
        hp_healed = min(hp_needed, gold_available)
        
        if hp_healed > 0:
            attacker.healthpoints += hp_healed
            attacker.gold -= hp_healed
            heal_message = f"Automatically healed {hp_healed} HP for {hp_healed} gold."
            fight_log.append(heal_message)
    
    if winner == "player":
        xp_gain = random.randint(npc.min_xp, npc.max_xp)
        gold_gain = random.randint(npc.min_gold, npc.max_gold)
        attacker.add_xp(xp_gain)
        attacker.gold += gold_gain
        attacker.reputation += npc.reputation
        character_item = CharacterItem(
                character_id=current_user.character.id,
                item_id=npc.weapon.id,
                equipped=False)
        db.session.add(character_item)
        fight_log.append(Markup(f"<span class='rare-drop'>You got an ultra rare drop: {npc.weapon.translation_key.replace('_', ' ').title()}!</span>"))
        fight_log.append(f"You gained {xp_gain} XP and {gold_gain} gold!")
    else:
        attacker.reputation -= npc.reputation
    
    npc.healthpoints = npc.max_healthpoints
    
    db.session.commit()
    
    return render_template('npc_fight_result.html',
                         translations=g.translations,
                         fight_log=fight_log,
                         winner=winner,
                         npc=npc,
                         xp_gain=xp_gain if winner == "player" else 0,
                         gold_gain=gold_gain if winner == "player" else 0,
                         reputation_change=npc.reputation if winner == "player" else -npc.reputation,
                         heal_message=heal_message)

@game_bp.route('/magic-shop')
@login_required
@not_jailed
def magic_shop():
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    magic_items = Item.query.filter_by(item_type='magic', is_npc_only=False).all()
    
    return render_template('magic_shop.html',
                         translations=g.translations,
                         magic_items=magic_items,
                         character=current_user.character)  

@game_bp.route('/admin/magic-items')
@login_required
@admin_required
def manage_magic_items():
    magic_items = Item.query.filter_by(item_type='magic').all()
    return render_template('manage_magic_items.html',
                         translations=g.translations,
                         magic_items=magic_items)

@game_bp.route('/admin/magic-items/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_magic_item():
    if request.method == 'POST':
        try:
            translation_key = request.form['translation_key']
            price = int(request.form['price'])
            price_type = request.form.get('price_type', 'gold')
            min_level = int(request.form['min_level'])
            
            stats = {
                'gold': int(request.form.get('gold', 0)),
                'diamonds': int(request.form.get('diamonds', 0)),
                'health': int(request.form.get('health', 0)),
                'resource': int(request.form.get('resource', 0)),
                'revives': 'revives' in request.form
            }
            
            stats = {k: v for k, v in stats.items() if v or k == 'revives'}
            
            new_item = Item(
                item_type='magic',
                translation_key=translation_key,
                price=price,
                price_type=price_type,
                min_level=min_level,
                stats=stats
            )
            
            db.session.add(new_item)
            db.session.commit()
            
            update_magic_item_translations(new_item)
            flash("Magic item added successfully!", 'success')
            return redirect(url_for('game.manage_magic_items'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding magic item: {str(e)}", 'error')
    
    return render_template('admin_add_magic_item.html',
                         translations=g.translations)

@game_bp.route('/admin/magic-items/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_magic_item(item_id):
    item = Item.query.get_or_404(item_id)
    
    if request.method == 'POST':
        try:
            old_translation_key = item.translation_key
            item.translation_key = request.form['translation_key']
            item.price = int(request.form['price'])
            item.min_level = int(request.form['min_level'])
            
            stats = {
                'gold': int(request.form.get('gold', 0)),
                'diamonds': int(request.form.get('diamonds', 0)),
                'health': int(request.form.get('health', 0)),
                'resource': int(request.form.get('resource', 0)),
                'revives': 'revives' in request.form
            }
            
            item.stats = {k: v for k, v in stats.items() if v or k == 'revives'}
            
            db.session.commit()
            
            if old_translation_key != item.translation_key:
                update_magic_item_translations(item)
                remove_magic_item_translation(old_translation_key)
            
            flash("Magic item updated successfully!", 'success')
            return redirect(url_for('game.manage_magic_items'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating magic item: {str(e)}", 'error')
    
    return render_template('admin_edit_magic_item.html',
                         translations=g.translations,
                         item=item)

@game_bp.route('/admin/magic-items/delete/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def delete_magic_item(item_id):
    item = Item.query.get_or_404(item_id)
    
    CharacterItem.query.filter_by(item_id=item.id).delete()
    
    db.session.delete(item)
    db.session.commit()
    
    flash("Magic item deleted successfully!", 'success')
    return redirect(url_for('game.manage_magic_items'))

@game_bp.route('/use-item/<int:item_id>', methods=['POST'])
@login_required
@not_jailed
def use_item(item_id):
    if not current_user.character:
        return redirect(url_for('game.dashboard'))
    
    character = current_user.character
    character_item = CharacterItem.query.filter_by(
        character_id=character.id,
        item_id=item_id
    ).first_or_404()
    
    item = character_item.item
    
    if item.item_type != 'magic':
        flash("This item cannot be used", 'error')
        return redirect(url_for('game.shop', tab='magic'))
    
    current_tab = request.form.get('current_tab', 'magic')
    
    message = []
    
    if 'gold' in item.stats and item.stats['gold'] > 0:
        character.gold += item.stats['gold']
        message.append(f"Gained {item.stats['gold']} gold")
    
    if 'diamonds' in item.stats and item.stats['diamonds'] > 0:
        character.diamonds += item.stats['diamonds']
        message.append(f"Gained {item.stats['diamonds']} diamonds")
    
    if 'health' in item.stats and item.stats['health'] > 0:
        heal_amount = int(character.max_healthpoints * (item.stats['health'] / 100))
        character.healthpoints = min(character.max_healthpoints, character.healthpoints + heal_amount)
        message.append(f"Restored {item.stats['health']}% health ({heal_amount} HP)")
    
    if 'resource' in item.stats and item.stats['resource'] > 0:
        resource_amount = int(character.resource_max * (item.stats['resource'] / 100))
        character.resource = min(character.resource_max, character.resource + resource_amount)
        message.append(f"Restored {item.stats['resource']}% resource ({resource_amount} points)")
    
    if 'revives' in item.stats and item.stats['revives'] and character.is_dead:
        character.is_dead = False
        character.healthpoints = character.max_healthpoints
        message.append("You have been revived!")
    
    db.session.delete(character_item)
    db.session.commit()
    
    flash("Item used: " + ", ".join(message), 'success')
    return redirect(url_for('game.shop', tab=current_tab))

def update_magic_item_translations(item):
    """Update the translations JSON file with new magic item data"""
    try:
        lang = current_app.config['DEFAULT_LANGUAGE']
        file_path = os.path.join(current_app.root_path, 'locales', f'{lang}.json')
        
        with open(file_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        
        if 'game' not in translations:
            translations['game'] = {}
        if 'items' not in translations['game']:
            translations['game']['items'] = {}
        if 'magic' not in translations['game']['items']:
            translations['game']['items']['magic'] = {}
        
        friendly_name = item.translation_key.replace('_', ' ').title()
        
        translations['game']['items']['magic'][item.translation_key] = {
            'name': friendly_name,
            'description': f"A {friendly_name.lower()}"
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to update magic item translations: {str(e)}")
        raise

def remove_magic_item_translation(translation_key):
    """Remove a magic item from the translations file"""
    try:
        lang = current_app.config['DEFAULT_LANGUAGE']
        file_path = os.path.join('locales', f'{lang}.json')
        
        with open(file_path, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        
        if 'game' in translations and 'items' in translations['game'] and 'magic' in translations['game']['items']:
            translations['game']['items']['magic'].pop(translation_key, None)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False, indent=4)
            
    except Exception as e:
        current_app.logger.error(f"Failed to remove magic item translation: {str(e)}")
