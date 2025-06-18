from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import json
from sqlalchemy import func, event
from datetime import timedelta
from cache_helpers import invalidate_rankings_cache

db = SQLAlchemy()

class Item(db.Model):
    __tablename__ = 'items'
    
    id = db.Column(db.Integer, primary_key=True)
    item_type = db.Column(db.String(20), nullable=False)
    armor_type = db.Column(db.String(20), nullable=True)
    translation_key = db.Column(db.String(100), nullable=False, unique=True)
    price = db.Column(db.Integer, nullable=False)
    price_type = db.Column(db.String(10), default='gold')
    min_level = db.Column(db.Integer, default=1)
    stats = db.Column(db.JSON)
    required_attribute = db.Column(db.String(20), nullable=True)
    required_amount = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_npc_only = db.Column(db.Boolean, default=False)
    is_rare_drop = db.Column(db.Boolean, default=False)
    drop_rate = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f'<Item {self.translation_key}>'

class CharacterItem(db.Model):
    __tablename__ = 'character_items'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    equipped = db.Column(db.Boolean, default=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    character = db.relationship('Character', backref='items')
    item = db.relationship('Item')

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(128))
    language = db.Column(db.String(5), default='pt-BR')
    fediverse_id = db.Column(db.String(120), unique=True, name='uq_user_fediverse_id')
    fediverse_data = db.Column(db.JSON)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    
    character = db.relationship('Character', back_populates='user', uselist=False, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
        
    def is_online(self):
        """Check if the user is currently online"""
        if not self.last_activity:
            return False
        return (datetime.utcnow() - self.last_activity).total_seconds() < 300  # 5 minutes
    
    def set_fediverse_data(self, data):
        """Helper method to safely set fediverse_data"""
        def serialize(obj):
            if isinstance(obj, (str, int, float, bool, list, dict, type(None))):
                return obj
            elif hasattr(obj, 'isoformat'):
                return obj.isoformat()
            elif hasattr(obj, '__dict__'):
                return {k: serialize(v) for k, v in obj.__dict__.items()}
            return str(obj)
        
        self.fediverse_data = data

class FediverseInstance(db.Model):
    __tablename__ = 'fediverse_instances'
    
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, name='uq_fediverse_domain')
    client_id = db.Column(db.String(255))
    client_secret = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def normalize_name(name):
    """Normalize a name by removing accents and converting to lowercase"""
    import unicodedata
    if not name:
        return ''
    normalized = unicodedata.normalize('NFKD', name.lower())
    normalized = normalized.encode('ascii', 'ignore').decode('ascii')
    return normalized

class Character(db.Model):
    __tablename__ = 'characters'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    normalized_name = db.Column(db.String(80), nullable=False)
    faction = db.Column(db.String(20), nullable=False)
    level = db.Column(db.Integer, default=1)
    current_xp = db.Column(db.Integer, default=0)
    xp_to_next_level = db.Column(db.Integer, default=150)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_dead = db.Column(db.Boolean, default=False)
    last_fight_time = db.Column(db.DateTime)
    last_killed_id = db.Column(db.Integer, db.ForeignKey('characters.id'))
    last_killed = db.relationship('Character', foreign_keys=[last_killed_id], remote_side=[id], post_update=True)  
    last_killed_by_id = db.Column(db.Integer, db.ForeignKey('characters.id'))
    last_killed_by = db.relationship('Character', foreign_keys=[last_killed_by_id], remote_side=[id], post_update=True)
    motto = db.Column(db.String(200), nullable=True)
    reputation = db.Column(db.Integer, default=0)
    bank_gold = db.Column(db.Integer, default=0)
    mining_level = db.Column(db.Integer, default=10, nullable=False)
    diamonds = db.Column(db.Integer, default=0, nullable=False)
    is_jailed = db.Column(db.Boolean, default=False)
    current_jail_id = db.Column(db.Integer, db.ForeignKey('jail.id'))
    current_jail = db.relationship('Jail', foreign_keys=[current_jail_id])
    #last_quest_refresh = db.Column(db.DateTime, nullable=True, default=None)
    
    # Attributes
    destreza = db.Column(db.Float, default=10)
    forca = db.Column(db.Float, default=10)
    inteligencia = db.Column(db.Float, default=10)
    devocao = db.Column(db.Float, default=10)
    _healthpoints = db.Column('healthpoints', db.Integer)
    gold = db.Column(db.Integer, default=500, nullable=False)
    resource = db.Column(db.Integer, default=75)
    resource_max = db.Column(db.Integer, default=75)
    last_resource_update = db.Column(db.DateTime, default=datetime.utcnow)
    pvp_kills = db.Column(db.Integer, default=0)
    deaths = db.Column(db.Integer, default=0)
    mining_streak = db.Column(db.Integer, default=0)
    last_mine_date = db.Column(db.DateTime)
    
    destreza_per_level = db.Column(db.Float, default=1.0)
    forca_per_level = db.Column(db.Float, default=1.0)
    inteligencia_per_level = db.Column(db.Float, default=1.0)
    devocao_per_level = db.Column(db.Float, default=1.0)
    healthpoints_per_level = db.Column(db.Integer, default=10)
    
    user = db.relationship('User', back_populates='character')
    
    def __init__(self, **kwargs):
        super(Character, self).__init__(**kwargs)
        
        self._healthpoints = self.max_healthpoints
    
    def calculate_xp_to_next_level(self):
        return 150 * (self.level ** 2)
    
    def add_xp(self, amount):
        self.current_xp += amount
        while self.current_xp >= self.xp_to_next_level:
            self.current_xp -= self.xp_to_next_level
            self.level_up()
        return self
    
    def level_up(self):
        self.level += 1
        self.xp_to_next_level = self.calculate_xp_to_next_level()
        self.resource = min(self.resource + 1, self.resource_max)
    
        self.destreza += self.destreza_per_level
        self.forca += self.forca_per_level
        self.inteligencia += self.inteligencia_per_level
        self.devocao += self.devocao_per_level
    
       
        self.healthpoints = self.max_healthpoints
    
        return self
    
    def update_resources(self):
        """Update resources based on time passed"""
        now = datetime.utcnow()
        time_since_update = (now - self.last_resource_update).total_seconds()
        
        if time_since_update >= 21 * 3600:
            self.resource = self.resource_max
            self.last_resource_update = now
        else:
            increments = int(time_since_update // (30 * 60))
            if increments > 0:
                self.resource = min(self.resource + increments, self.resource_max)
                self.last_resource_update = now - timedelta(
                    seconds=time_since_update % (30 * 60))
        
        return self
        
    def heal(self, amount, cost):
        if self.gold >= cost and amount > 0:
            self.gold -= cost
            self.healthpoints = min(self.healthpoints + amount, self.max_healthpoints)
            if self.healthpoints > 0 and self.is_dead:
                self.is_dead = False
            return True
        return False
         
    def revive(self):
        self.is_dead = False
        self.healthpoints = self.max_healthpoints
        self.last_revive = datetime.utcnow()
        
    def refresh_resources(self):
        self.resource = self.resource_max
    
    def change_reputation(self, amount):
        """Safely modify reputation with logging"""
        old_rep = self.reputation
        self.reputation = max(-10, self.reputation + amount)
        print(f"Reputation change: {self.name} {old_rep} -> {self.reputation} ({'+' if amount >=0 else ''}{amount})")
        return self
    
    @property
    def max_healthpoints(self):
        from flask import current_app
        faction_stats = current_app.config['FACTION_STATS'].get(self.faction, {})
        base_hp = faction_stats.get('stats', {}).get('healthpoints', {}).get('base', 20)
        
        level_hp = self.level * 5
        
        armor_bonus = 0
        for item in self.items:
            if item.equipped and item.item.item_type == 'armor' and 'health_bonus' in item.item.stats:
                armor_bonus += item.item.stats['health_bonus']
    
        return base_hp + level_hp + armor_bonus
    
    @property
    def healthpoints(self):
        return self._healthpoints
    
    @healthpoints.setter
    def healthpoints(self, value):
        self._healthpoints = min(value, self.max_healthpoints)
        if self._healthpoints <= 0:
            self.is_dead = True  

def setup_ranking_invalidation():
    """Setup SQLAlchemy event listeners"""
    from sqlalchemy import event
    
    def auto_invalidate_rankings(mapper, connection, target):
        """SQLAlchemy event listener"""
        if isinstance(target, Character):
            invalidate_rankings_cache()
    
    event.listen(Character, 'after_insert', auto_invalidate_rankings)
    event.listen(Character, 'after_update', auto_invalidate_rankings)
    event.listen(Character, 'after_delete', auto_invalidate_rankings)

setup_ranking_invalidation()          

class BattleLog(db.Model):
    __tablename__ = 'battle_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    attacker_id = db.Column(db.Integer, db.ForeignKey('characters.id'))
    defender_id = db.Column(db.Integer, db.ForeignKey('characters.id'))
    winner_id = db.Column(db.Integer, db.ForeignKey('characters.id'))
    log = db.Column(db.JSON)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    attacker_name = db.Column(db.String(80))
    defender_name = db.Column(db.String(80))
    winner_name = db.Column(db.String(80))
    attacker_faction = db.Column(db.String(20))
    defender_faction = db.Column(db.String(20))
    winner_faction = db.Column(db.String(20))
    attacker_level = db.Column(db.Integer)
    defender_level = db.Column(db.Integer)
    attacker_reputation_change = db.Column(db.Integer, default=0)
    defender_reputation_change = db.Column(db.Integer, default=0)
    xp_gained = db.Column(db.Integer, default=0)
    resource_cost = db.Column(db.Integer, default=1)
    attacker_gold_change = db.Column(db.Integer, default=0)
    defender_gold_change = db.Column(db.Integer, default=0)
    
    attacker = db.relationship('Character', foreign_keys=[attacker_id])
    defender = db.relationship('Character', foreign_keys=[defender_id])
    winner = db.relationship('Character', foreign_keys=[winner_id])

class MiningLottery(db.Model):
    __tablename__ = 'mining_lottery'
    
    id = db.Column(db.Integer, primary_key=True)
    current_gold = db.Column(db.Integer, default=0)
    current_diamonds = db.Column(db.Integer, default=20)
    last_draw_time = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LotteryEntry(db.Model):
    __tablename__ = 'lottery_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    entry_time = db.Column(db.DateTime, default=datetime.utcnow)
    cost = db.Column(db.Integer, nullable=False) 
    character = db.relationship('Character', backref='lottery_entries')

class LotteryWinner(db.Model):
    __tablename__ = 'lottery_winners'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    win_time = db.Column(db.DateTime, default=datetime.utcnow)
    gold_won = db.Column(db.Integer)
    diamonds_won = db.Column(db.Integer)
    
    character = db.relationship('Character', backref='lottery_wins')

class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    is_admin_message = db.Column(db.Boolean, default=False)
    parent_message_id = db.Column(db.Integer, db.ForeignKey('messages.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=30))
    
    sender = db.relationship('Character', foreign_keys=[sender_id], backref='sent_messages')
    recipient = db.relationship('Character', foreign_keys=[recipient_id], backref='received_messages')
    parent_message = db.relationship('Message', remote_side=[id], backref='replies')
    
    def mark_as_read(self):
        self.is_read = True
        db.session.commit()
        
class MessageReport(db.Model):
    __tablename__ = 'message_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved = db.Column(db.Boolean, default=False)
    
    message = db.relationship('Message', backref='reports')
    reporter = db.relationship('Character', foreign_keys=[reporter_id])

class Jail(db.Model):
    __tablename__ = 'jail'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    duration = db.Column(db.Integer)
    duration_unit = db.Column(db.String(10))
    real_reason = db.Column(db.Text, nullable=False)
    game_reason = db.Column(db.Text, nullable=False)
    is_released = db.Column(db.Boolean, default=False)
    
    character = db.relationship('Character', foreign_keys=[character_id])
    admin = db.relationship('Character', foreign_keys=[admin_id])

class NPC(db.Model):
    __tablename__ = 'npcs'
    
    id = db.Column(db.Integer, primary_key=True)
    translation_key = db.Column(db.String(100), nullable=False, unique=True)
    level = db.Column(db.Integer, default=1)
    healthpoints = db.Column(db.Integer, default=100)
    max_healthpoints = db.Column(db.Integer, default=100)
    weapon_id = db.Column(db.Integer, db.ForeignKey('items.id'))
    armor_id = db.Column(db.Integer, db.ForeignKey('items.id'))
    min_xp = db.Column(db.Integer, default=10)
    max_xp = db.Column(db.Integer, default=20)
    min_gold = db.Column(db.Integer, default=5)
    max_gold = db.Column(db.Integer, default=10)
    image = db.Column(db.String(100), default='npc.webp')
    reputation = db.Column(db.Integer, default=0)
    inteligencia = db.Column(db.Float, default=10)
    destreza = db.Column(db.Float, default=10)
    forca = db.Column(db.Float, default=10)
    devocao = db.Column(db.Float, default=10)
    faction = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    weapon = db.relationship('Item', foreign_keys=[weapon_id])
    armor = db.relationship('Item', foreign_keys=[armor_id])

class Quest(db.Model):
    @property
    def title(self):
        from flask import g
        try:
            if hasattr(g, 'translations'):
                return g.translations['game']['quests'].get(self.translation_key, {}).get('title', self.translation_key)
            return self.translation_key
        except:
            return self.translation_key
            
    @property
    def description(self):
        from flask import g
        try:
            if hasattr(g, 'translations'):
                return g.translations['game']['quests'].get(self.translation_key, {}).get('description', '')
            return ''
        except:
            return ''
	
    __tablename__ = 'quests'
    
    id = db.Column(db.Integer, primary_key=True)
    translation_key = db.Column(db.String(100), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_unique = db.Column(db.Boolean, default=False)
    spawn_chance = db.Column(db.Float, default=1.0)  # Relative weight for random selection
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    objectives = db.relationship('QuestObjective', backref='quest', cascade='all, delete-orphan')
    rewards = db.relationship('QuestReward', backref='quest', cascade='all, delete-orphan')
   

class QuestObjective(db.Model):
    __tablename__ = 'quest_objectives'
    
    id = db.Column(db.Integer, primary_key=True)
    quest_id = db.Column(db.Integer, db.ForeignKey('quests.id'), nullable=False)
    objective_type = db.Column(db.String(50), nullable=False)  # e.g., 'kill_players', 'mine_resources'
    target_value = db.Column(db.String(100))  # Could be faction name, NPC ID, etc.
    amount_required = db.Column(db.Integer, default=1)

class QuestReward(db.Model):
    __tablename__ = 'quest_rewards'
    
    id = db.Column(db.Integer, primary_key=True)
    quest_id = db.Column(db.Integer, db.ForeignKey('quests.id'), nullable=False)
    reward_type = db.Column(db.String(50), nullable=False)  # e.g., 'gold', 'diamonds'
    amount = db.Column(db.Integer, default=0)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=True)
    target_value = db.Column(db.String(100), nullable=True)  # For attribute rewards
    attribute_type = db.Column(db.String(50), nullable=True) # 'forca', 'destreza', 'inteligencia', 'devocao'
    attribute_amount = db.Column(db.Float, nullable=True) # For attribute rewards (amount to increase)
    
    item = db.relationship('Item')

class PlayerQuest(db.Model):
    @property
    def status_text(self):
        if self.is_completed:
            return "Completed"
        elif self.is_failed:
            return "Failed"
        return "In Progress"

    __tablename__ = 'player_quests'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    quest_id = db.Column(db.Integer, db.ForeignKey('quests.id'), nullable=False)
    progress = db.Column(db.Integer, default=0)
    is_completed = db.Column(db.Boolean, default=False)
    is_failed = db.Column(db.Boolean, default=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    last_quest_refresh = db.Column(db.DateTime, nullable=True, default=None)
    character = db.relationship('Character', backref='quests')
    quest = db.relationship('Quest')
    quest_progress = db.relationship('QuestProgress', lazy=True, cascade="all, delete-orphan", primaryjoin="PlayerQuest.id == QuestProgress.player_quest_id", back_populates='player_quest')

class QuestProgress(db.Model):
    __tablename__ = 'quest_progress'
    
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('characters.id'), nullable=False)
    objective_id = db.Column(db.Integer, db.ForeignKey('quest_objectives.id'), nullable=False)
    player_quest_id = db.Column(db.Integer, db.ForeignKey('player_quests.id'), nullable=False)
    progress_value = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    player_quest = db.relationship('PlayerQuest', foreign_keys=[player_quest_id], back_populates='quest_progress')
    
    character = db.relationship('Character', backref='quest_progress')
    objective = db.relationship('QuestObjective', backref='progress')
