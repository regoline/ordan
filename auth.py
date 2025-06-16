from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, session, g, current_app
)
from werkzeug.security import generate_password_hash, check_password_hash
from database import db, User
from flask_babel import gettext
from flask_login import login_user, logout_user, login_required, current_user
from mastodon import Mastodon
from datetime import datetime  # Add this with other imports
from database import FediverseInstance  # Add this import
import urllib.parse
import os
import json

NGROK_URL = "https://25c7-2804-868-d047-9519-74d7-52d7-f35b-2cae.ngrok-free.app"

auth_bp = Blueprint('auth', __name__)

def load_translations(lang):
    """Load translations for the specified language"""
    translations = {
        'general': {},
        'auth': {},
        'errors': {},
        'forms': {},
        'navigation': {},
        'game': {
            'items': {
                'weapon': {},
                'armor': {
                    'head': {},
                    'body': {},
                    'gloves': {},
                    'pants': {},
                    'boots': {}
                },
                'magic': {}
            },
            'npcs': {},
            'factions': {},
            'attributes': {},
            'academy': {},
            'market': {},
            'fights': {},
            'arena': {},
            'bank': {},
            'mine': {},
            'lottery': {},
            'character_stats': {},
            'buildings': {},
            'quests': {
				'attributes': {}
			}
        },
        'dashboard': {},
        'character_creation': {},
        'authentication': {},
        'lore': {},
        'search': {},
        'fight': {},
        'rankings': {},
        'shop': {},
        'admin': {}
    }

    base_path = os.path.join('locales', lang)
    
    if not os.path.exists(base_path):
        return translations
    
    # Load general translations
    general_path = os.path.join(base_path, 'general.json')
    if os.path.exists(general_path):
        with open(general_path, 'r', encoding='utf-8') as f:
            translations['general'] = json.load(f)
    
    # Load other top-level files
    for filename in ['auth', 'errors', 'forms', 'navigation', 'dashboard', 
                    'character_creation', 'authentication', 'lore', 'search',
                    'fight', 'rankings', 'shop', 'admin']:
        file_path = os.path.join(base_path, f"{filename}.json")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                translations[filename] = json.load(f)
    
    # Load game translations
    game_path = os.path.join(base_path, 'game')
    if os.path.exists(game_path):
        # Load game items
        items_path = os.path.join(game_path, 'items')
        if os.path.exists(items_path):
            weapon_path = os.path.join(items_path, 'weapons.json')
            if os.path.exists(weapon_path):
                with open(weapon_path, 'r', encoding='utf-8') as f:
                    translations['game']['items']['weapon'] = json.load(f)
            
            armor_path = os.path.join(items_path, 'armor.json')
            if os.path.exists(armor_path):
                with open(armor_path, 'r', encoding='utf-8') as f:
                    armor_data = json.load(f)
                    for slot in ['head', 'body', 'gloves', 'pants', 'boots']:
                        translations['game']['items']['armor'][slot] = armor_data.get(slot, {})
            
            magic_path = os.path.join(items_path, 'magic.json')
            if os.path.exists(magic_path):
                with open(magic_path, 'r', encoding='utf-8') as f:
                    translations['game']['items']['magic'] = json.load(f)
        
        # Load other game files
        for filename in ['npcs', 'factions', 'attributes', 'academy', 'market', 'fights', 'arena', 'lottery', 'mine', 'bank', 'quests']:
            file_path = os.path.join(game_path, f"{filename}.json")
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    translations['game'][filename] = json.load(f)
    
    return translations

def get_current_language():
    """Get language from user preference, session, or default"""
    if current_user.is_authenticated and current_user.language:
        return current_user.language
    return session.get('language', current_app.config['DEFAULT_LANGUAGE'])

@auth_bp.route('/set-language/<language>')
def set_language(language):
    """Handle language switching"""
    if language in current_app.config['AVAILABLE_LANGUAGES']:
        session['language'] = language
        if current_user.is_authenticated:
            current_user.language = language
            db.session.commit()
        #flash(g.translations['language']['language_changed'])
    return redirect(request.referrer or url_for('auth.login'))

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            # Validate inputs
            if not all([username, password]):
                flash(g.translations['errors']['all_fields_required'])
                return redirect(url_for('auth.signup'))
                
            if User.query.filter_by(username=username).first():
                flash(g.translations['errors']['username_exists'])
                return redirect(url_for('auth.signup'))

            # Create user with IP tracking
            new_user = User(
                username=username,
                email=None,
                language=current_lang,
                ip_address=request.remote_addr,
                created_at=datetime.utcnow()
            )
            new_user.set_password(password)
            
            # Make first user admin automatically
            if User.query.count() == 0:
                new_user.is_admin = True
            
            db.session.add(new_user)
            db.session.commit()
            
            login_user(new_user)
            return redirect(url_for('game.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Account creation failed: {str(e)}")
            flash(g.translations['errors']['account_creation_failed'])
            return redirect(url_for('auth.signup'))
    
    return render_template('signup.html',
                         translations=g.translations,
                         languages=current_app.config['AVAILABLE_LANGUAGES'],
                         current_language=current_lang)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = bool(request.form.get('remember'))
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Update last login time and IP
            user.last_login_at = datetime.utcnow()
            user.last_activity = datetime.utcnow()
            user.ip_address = request.remote_addr
            db.session.commit()
            
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('game.dashboard'))
        else:
            flash(g.translations['errors']['invalid_credentials'])
    
    return render_template('login.html',
                         translations=g.translations,
                         languages=current_app.config['AVAILABLE_LANGUAGES'],
                         current_language=current_lang)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    #flash(g.translations['auth']['logout_success'])
    return redirect(url_for('auth.login'))

@auth_bp.route('/fediverse/login', methods=['GET', 'POST'])
def fediverse_login():
    current_lang = get_current_language()
    g.translations = load_translations(current_lang)
    
    if request.method == 'POST':
        print("\n=== Starting Fediverse Login ===")
        identifier = request.form.get('fediverse_identifier')
        print(f"Received identifier: {identifier}")
        
        if not identifier:
            flash(g.translations['errors']['fediverse_identifier_required'])
            return redirect(url_for('auth.fediverse_login'))
        
        try:
            # Handle instance URL
            if '@' in identifier:
                instance_url = 'https://' + identifier.split('@')[-1]
            else:
                instance_url = identifier if identifier.startswith('http') else f'https://{identifier}'
            
            print(f"Instance URL: {instance_url}")
            session['fediverse_instance'] = instance_url
            domain = urllib.parse.urlparse(instance_url).netloc
            print(f"Domain: {domain}")
            
            # Check cached credentials
            instance = FediverseInstance.query.filter_by(domain=domain).first()
            
            # Define callback URL - must be EXACTLY the same everywhere
            callback_url = f"{NGROK_URL}/fediverse/callback"
            
            if instance:
                print("Using cached credentials")
                client_id = instance.client_id
                client_secret = instance.client_secret
            else:
                print("Registering new application")
                print(f"Registering callback URL: {callback_url}")
                
                # Minimal scope needed just for authentication
                client_id, client_secret = Mastodon.create_app(
                    'YourRPGApp',
                    api_base_url=instance_url,
                    scopes=['read:accounts'],  # Minimal scope for authentication only
                    redirect_uris=callback_url  # Single string, not list
                )
                print(f"Created app - Client ID: {client_id[:10]}...")
                
                # Cache the credentials
                new_instance = FediverseInstance(
                    domain=domain,
                    client_id=client_id,
                    client_secret=client_secret
                )
                db.session.add(new_instance)
                db.session.commit()
                print("Saved credentials to database")
            
            # Store temporarily
            session['fediverse_client_id'] = client_id
            session['fediverse_client_secret'] = client_secret
            
            # Generate auth URL with minimal scope
            auth_url = (
                f"{instance_url}/oauth/authorize?"
                f"client_id={client_id}&"
                f"response_type=code&"
                f"redirect_uri={urllib.parse.quote(callback_url)}&"
                f"scope=read:accounts"  # Only requesting account info
            )
            
            print(f"Redirecting to auth URL: {auth_url}")
            return redirect(auth_url)
        
        except Exception as e:
            print(f"\n!!! Fediverse login error: {str(e)}")
            flash(gettext('Fediverse login error'))
            return redirect(url_for('auth.fediverse_login'))
    
    return render_template('fediverse_login.html',
                         translations=g.translations)

@auth_bp.route('/fediverse/callback')
def fediverse_callback():
    print("\n=== Fediverse Callback ===")
    print("Request args:", request.args)
    
    if 'code' not in request.args:
        print("No code parameter in callback")
        flash(gettext('Fediverse authentication failed - no code received'))
        return redirect(url_for('auth.login'))
    
    try:
        instance_url = session['fediverse_instance']
        callback_url = f"{NGROK_URL}/fediverse/callback"
        
        mastodon = Mastodon(
            client_id=session['fediverse_client_id'],
            client_secret=session['fediverse_client_secret'],
            api_base_url=instance_url,
            request_timeout=30
        )
        
        access_token = mastodon.log_in(
            code=request.args['code'],
            redirect_uri=callback_url,
            scopes=['read:accounts']
        )
        
        account = mastodon.account_verify_credentials()
        fediverse_id = f"{account.id}@{urllib.parse.urlparse(instance_url).netloc}"
        
        # Find or create user
        user = User.query.filter_by(fediverse_id=fediverse_id).first()
        if not user:
            username = account.username
            if User.query.filter_by(username=username).first():
                username = f"{username}_{account.id}"
                
            user = User(
                username=username,
                fediverse_id=fediverse_id,
                language=session.get('language', current_app.config['DEFAULT_LANGUAGE']),
                email=None,
                password_hash=None
            )
            
            # Create a simple dict with just the essential account info
            account_data = {
                'id': account.id,
                'username': account.username,
                'acct': account.acct,
                'display_name': account.display_name,
                'avatar': account.avatar,
                'url': account.url
            }
            
            user.set_fediverse_data(account_data)
            
            db.session.add(user)
            db.session.commit()
        
        login_user(user)
        #flash(gettext('Login successful!'))
        return redirect(url_for('game.dashboard'))
    
    except Exception as e:
        print(f"Fediverse auth failed: {str(e)}")
        flash(gettext('Fediverse authentication failed'))
        return redirect(url_for('auth.login'))
