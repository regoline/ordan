from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import db, Message, Jail, Character

def check_jail_expirations():
    """Release players whose jail time has expired"""
    expired_jails = Jail.query.filter(
        Jail.is_released == False,
        db.func.datetime(Jail.start_time + db.func.cast(Jail.duration, db.Interval) * 60) <= datetime.utcnow()
    ).all()
    
    for jail in expired_jails:
        jail.character.is_jailed = False
        jail.is_released = True
        db.session.commit()

def reset_daily_quests():
    """Reset all daily quests (mark failed if not completed)"""
    from database import PlayerQuest, db
    from datetime import datetime, timedelta
    
    # Get all active quests that haven't been completed
    active_quests = PlayerQuest.query.filter(
        PlayerQuest.is_completed == False,
        PlayerQuest.is_failed == False,
        PlayerQuest.started_at < datetime.utcnow() - timedelta(hours=21)
    ).all()
    
    for quest in active_quests:
        quest.is_failed = True
        quest.completed_at = datetime.utcnow()
        # Apply reputation penalty
        quest.character.change_reputation(-2)
    
    db.session.commit()

def daily_refresh():
    """Perform the daily refresh (every 21 hours)"""
    now = datetime.utcnow()
    print(f"Running daily refresh at {now}")
    
    try:
        # First revive all dead players
        dead_players = Character.query.filter_by(is_dead=True).all()
        for player in dead_players:
            player.revive()
            print(f"Revived {player.name} (ID: {player.id})")
        
        # Then refresh resources
        all_players = Character.query.all()
        for player in all_players:
            player.resource = player.resource_max
            player.last_resource_update = now
            print(f"Refreshed resources for {player.name}")
        
        # Finally reset quests
        reset_daily_quests()
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Daily refresh failed: {str(e)}")

def periodic_revives():
    """Perform periodic revives (every 3 hours)"""
    now = datetime.utcnow()
    print(f"Running periodic revive at {now}")
    
    dead_players = Character.query.filter_by(is_dead=True).all()
    for player in dead_players:
        player.revive()
    
    db.session.commit()
    print(f"Periodic revive completed at {now} - revived {len(dead_players)} players")



def cleanup_expired_messages():
    """Delete messages older than 30 days"""
    expired_messages = Message.query.filter(
        Message.expires_at <= datetime.utcnow()
    ).all()
    
    for message in expired_messages:
        db.session.delete(message)
    
    db.session.commit()

def init_scheduler(app):
    scheduler = BackgroundScheduler(timezone="UTC")
    
    # Calculate next run times to align with the intervals
    now = datetime.utcnow()
    
    # Daily refresh every 21 hours
    next_refresh = now + timedelta(hours=21 - (now.hour % 21), minutes=-now.minute, seconds=-now.second)
    scheduler.add_job(
        func=daily_refresh,
        trigger="interval",
        hours=21,
        next_run_time=next_refresh
    )
    
    # Periodic revives every 3 hours
    next_revive = now + timedelta(hours=3 - (now.hour % 3), minutes=-now.minute, seconds=-now.second)
    scheduler.add_job(
        func=periodic_revives,
        trigger="interval",
        hours=3,
        next_run_time=next_revive
    )
    
    # Other jobs
    scheduler.add_job(func=cleanup_expired_messages, trigger="interval", hours=24)
    scheduler.add_job(func=check_jail_expirations, trigger="interval", minutes=5)
    
    scheduler.start()
    return scheduler
