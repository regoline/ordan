import os
import json

def get_available_languages():
    """Dynamically discover available languages from locales folder"""
    locales_dir = os.path.join(os.path.dirname(__file__), 'locales')
    languages = {}
    
    if os.path.exists(locales_dir):
        for dir_name in os.listdir(locales_dir):
            lang_dir = os.path.join(locales_dir, dir_name)
            if os.path.isdir(lang_dir):
                # Check if it's a valid language directory by looking for general.json
                if os.path.exists(os.path.join(lang_dir, 'general.json')):
                    try:
                        with open(os.path.join(lang_dir, 'general.json'), 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            lang_name = data.get('language_name', dir_name)
                            languages[dir_name] = lang_name
                    except (json.JSONDecodeError, IOError):
                        continue
                    
    return languages or {'en': 'English'}  # Fallback to English if no locales found

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///game.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AVAILABLE_LANGUAGES = get_available_languages()
    DEFAULT_LANGUAGE = 'pt-BR' if 'pt-BR' in AVAILABLE_LANGUAGES else next(iter(AVAILABLE_LANGUAGES))
    MASTODON_CLIENT_ID = os.getenv('MASTODON_CLIENT_ID')
    MASTODON_CLIENT_SECRET = os.getenv('MASTODON_CLIENT_SECRET')
    MASTODON_BASE_URL = 'https://mastodon.social'  # Or let users choose their instance
