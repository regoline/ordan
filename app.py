# Core Flask
from flask import Flask, request, session, g
from flask_migrate import Migrate

# Extensions
from flask_babel import Babel, gettext as _
from flask_login import LoginManager, current_user
from flask.cli import with_appcontext

# Local imports
from config import Config
from database import db, User
from auth import auth_bp
from game import game_bp
import os
import click
import json
from flask_caching import Cache
from cache_helpers import cache  # Add this import
from tasks import init_scheduler

def load_faction_stats(app):
    config_path = os.path.join(app.root_path, 'config', 'factions.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)['factions']
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        app.logger.error(f"Failed to load faction stats: {str(e)}")
        return {}

def load_translation_file(lang):
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
            'online': {},
            'character_stats':{},
            'buildings':[],
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
    
    general_path = os.path.join(base_path, 'general.json')
    if os.path.exists(general_path):
        with open(general_path, 'r', encoding='utf-8') as f:
            translations['general'] = json.load(f)
    
    for filename in ['auth', 'errors', 'forms', 'navigation', 'dashboard', 
                    'character_creation', 'authentication', 'lore', 'search',
                    'fight', 'rankings', 'shop', 'admin']:
        file_path = os.path.join(base_path, f"{filename}.json")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                translations[filename] = json.load(f)
    
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
        
        for filename in ['npcs', 'factions', 'attributes', 'academy', 'market', 'fights', 'arena', 'lottery', 'mine', 'bank','quests']:
            file_path = os.path.join(game_path, f"{filename}.json")
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    translations['game'][filename] = json.load(f)
        
        buildings_path = os.path.join(game_path, 'buildings.json')
        if os.path.exists(buildings_path):
            with open(buildings_path, 'r', encoding='utf-8') as f:
                buildings_data = json.load(f)
                translations['game']['buildings'] = buildings_data.get('buildings', [])
        
    
    return translations

@click.command('init-db')
@with_appcontext
def init_db_command():
    """Initialize the database tables."""
    db.create_all()
    print("Database tables created.")

@click.command('list-users')
@with_appcontext
def list_users_command():
    """List all users with admin status"""
    users = User.query.order_by(User.id).all()
    for user in users:
        print(f"{user.id}: {user.username} (Admin: {user.is_admin})")

@click.command('set-admin')
@click.argument('username')
@with_appcontext
def set_admin_command(username):
    """Set a user as admin"""
    user = User.query.filter_by(username=username).first()
    if not user:
        print(f"User {username} not found!")
        return
    
    user.is_admin = True
    db.session.commit()
    print(f"{username} is now an admin.")

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    db.init_app(app)
    migrate = Migrate(app, db)
    babel = Babel(app)
    cache.init_app(app, config={
        'CACHE_TYPE': 'SimpleCache',
        'CACHE_DEFAULT_TIMEOUT': 300
    })
    
    with app.app_context():
        config_path = os.path.join(app.root_path, 'config', 'factions.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                app.config['FACTION_STATS'] = json.load(f)['factions']
        except (FileNotFoundError, json.JSONDecodeError) as e:
            app.logger.error(f"Failed to load faction stats: {str(e)}")
            app.config['FACTION_STATS'] = {}
    
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    @app.context_processor
    def inject_user():
        return dict(current_user=current_user)
        
    @app.after_request
    def add_header(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(game_bp)
    
    app.cli.add_command(init_db_command)
    app.cli.add_command(list_users_command)
    app.cli.add_command(set_admin_command)
    
    @app.before_request
    def load_translations():
        lang = session.get('language', app.config['DEFAULT_LANGUAGE'])
        g.translations = load_translation_file(lang)
        
    init_scheduler(app)
        
    return app

if __name__ == '__main__':
    app = create_app()
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = init_scheduler(app)
    with app.app_context():
        db.create_all()
        admin = User.query.get(1)
        if admin and not admin.is_admin:
            admin.is_admin = True
            db.session.commit()
    app.run(ssl_context='adhoc', host='0.0.0.0', port=5000, debug=True)
