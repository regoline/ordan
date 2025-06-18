from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import db, Message, Jail, Character, PlayerQuest

def check_jail_expirations(app):
    """Release players whose jail time has expired"""
    with app.app_context(): # <--- ADDED
        expired_jails = Jail.query.filter(
            Jail.is_released == False,
            db.func.datetime(Jail.start_time + db.func.cast(Jail.duration, db.Interval) * 60) <= datetime.utcnow()
        ).all()
        
        for jail in expired_jails:
            jail.character.is_jailed = False
            jail.is_released = True
        db.session.commit()

def reset_daily_quests(app):
    """Reset all daily quests (mark failed if not completed)"""
    # Moved PlayerQuest and db import to the top of the file for consistency
    # from database import PlayerQuest, db # <-- REMOVE THIS LINE
    # from datetime import datetime, timedelta # <-- REMOVE THIS LINE (already at top)
    
    with app.app_context(): # <--- ADDED
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

def daily_refresh(app):
    """Perform the daily refresh (every 21 hours)"""
    now = datetime.utcnow()
    print(f"Running daily refresh at {now}")
    
    try:
        with app.app_context(): # <--- ADDED
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
            # reset_daily_quests() will also get its own context wrapper now
            # so calling it here is fine.
            reset_daily_quests(app) 
            
            db.session.commit() # This commits all changes made within this context
    except Exception as e:
        # Rollback only if db.session was started within this context block
        # For simplicity, if an error occurs, you might want to restart the service
        # or log more details if the rollback within a scheduled job is complex.
        # Ensure any db.session.add/delete operations are inside the try block.
        print(f"Daily refresh failed: {str(e)}")
        # If db.session.commit() fails, the session is usually rolled back automatically.
        # Explicit db.session.rollback() might be needed depending on error handling.
        # For simplicity, we assume the outer transaction handles this or the exception logs it.



def periodic_revives(app):
    """Perform periodic revives (every 3 hours)"""
    now = datetime.utcnow()
    print(f"Running periodic revive at {now}")
    
    with app.app_context():
        dead_players = Character.query.filter_by(is_dead=True).all()
        for player in dead_players:
            player.revive()
        db.session.commit() # Important: commit after all changes in the loop
    print(f"Periodic revive completed at {now} - revived {len(dead_players)} players")




def cleanup_expired_messages(app):
    """Delete messages older than 30 days"""
    with app.app_context():
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
        func=lambda: daily_refresh(app),
        trigger="interval",
        hours=21,
        next_run_time=next_refresh
    )
    
    # Periodic revives every 3 hours
    next_revive = now + timedelta(hours=3 - (now.hour % 3), minutes=-now.minute, seconds=-now.second)
    scheduler.add_job(
        func=lambda: periodic_revives(app),
        trigger="interval",
        hours=3,
        #next_run_time=datetime.utcnow()
        next_run_time=next_revive
    )
    
    # Other jobs
    scheduler.add_job(func=lambda: cleanup_expired_messages(app), trigger="interval", hours=24) # <--- MODIFIED
    scheduler.add_job(func=lambda: check_jail_expirations(app), trigger="interval", minutes=5) # <--- MODIFIED
    
    scheduler.start()
    return scheduler
