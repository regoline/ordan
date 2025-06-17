from flask_caching import Cache
from datetime import datetime


# Initialize empty cache that will be configured later
cache = Cache()

def get_cached_rankings():
    """Central function for all ranking calculations"""
    from database import Character
    from flask import current_app
    from collections import defaultdict
    
    now = datetime.utcnow()
    
    with current_app.app_context():
        # Get all character data
        chars = Character.query.all()
        
        # Individual player rankings
        player_data = [{
            'id': c.id,
            'name': c.name,
            'level': c.level,
            'current_xp': c.current_xp,
            'pvp_kills': c.pvp_kills,
            'deaths': c.deaths if hasattr(c, 'deaths') else 0,
            'reputation': c.reputation if hasattr(c, 'reputation') else 0,
            'faction': c.faction,
            'faction_color': get_faction_color(c.faction),
            'faction_image': f"{c.faction.lower()}.webp",
            'gold': c.gold,
            'bank_gold': c.bank_gold,
            'mining_level': c.mining_level,
            'diamonds': c.diamonds
            
        } for c in chars]
        
        # Faction statistics
        faction_stats = defaultdict(lambda: {'kills': 0, 'deaths': 0, 'count': 0})
        for char in chars:
            faction = char.faction
            faction_stats[faction]['kills'] += char.pvp_kills
            faction_stats[faction]['deaths'] += char.deaths if hasattr(char, 'deaths') else 0
            faction_stats[faction]['count'] += 1
        
        # Convert to list and sort
        faction_data = [{
            'name': faction,
            'kills': stats['kills'],
            'deaths': stats['deaths'],
            'count': stats['count'],
            'color': get_faction_color(faction),
            'image': f"{faction.lower()}.webp"
        } for faction, stats in faction_stats.items()]
        
        return {
            # Player rankings
            'xp': sorted(player_data, key=lambda x: (-x['current_xp'], x['name'])),
            'level': sorted(player_data, key=lambda x: (-x['level'], x['name'])),
            'kills': sorted(player_data, key=lambda x: (-x['pvp_kills'], x['name'])),
            'deaths': sorted(player_data, key=lambda x: (-x['deaths'], x['name'])),
            'reputation': sorted(player_data, key=lambda x: (-x['reputation'], x['name'])),
            'worst_reputation': sorted(player_data, key=lambda x: (x['reputation'], x['name'])),
            'gold': sorted(player_data, key=lambda x: (-x['gold'], x['name'])),
            'bank_gold': sorted(player_data, key=lambda x: (-x['bank_gold'], x['name'])),
            'mining_level': sorted(player_data, key=lambda x: (-x['mining_level'], x['name'])),
            'diamonds': sorted(player_data, key=lambda x: (-x['diamonds'], x['name'])),
            
            # Faction rankings
            'faction_kills': sorted(faction_data, key=lambda x: -x['kills']),
            'faction_deaths': sorted(faction_data, key=lambda x: -x['deaths']),
            'faction_count': sorted(faction_data, key=lambda x: -x['count']),
            'faction_pie': faction_data,  # For the pie chart
            
            'timestamp': now
        }

def get_faction_color(faction):
    """Helper to get faction color from your config"""
    color_map = {
        'veylan': '#6c3e8d',
        'urghan': '#87563e',
        'aureen': '#0a3e8d',
        'camyra': '#25743e'
    }
    return color_map.get(faction.lower(), '#cccccc')

def invalidate_rankings_cache():
    """Call this after any character stat changes"""
    cache.delete_memoized(get_cached_rankings)
