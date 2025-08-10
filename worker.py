import schedule
import time
from trello import TrelloClient
from main_app import create_app, db
from models import User, TrelloCard

# --- CONFIGURATION ---
# Use the same Trello API Key as your main app
TRELLO_API_KEY = "f4fa3a09b3308b68263da619dc1e00a8"
TRELLO_API_SECRET = "6359f7707fed6d520c2d4eb5b3719339ab782008b28338228cc99cd2b681c733"


def check_trello_tasks():
    """
    The main job for the worker. It checks the status of all tracked Trello cards.
    """
    print(f"--- Running accountability check at {time.ctime()} ---")

    # The worker needs an app context to interact with the database
    app = create_app()
    with app.app_context():
        # Find all users who have connected their Trello account
        users_with_trello = User.query.filter(User.trello_credentials != None).all()

        if not users_with_trello:
            print("No users with Trello integrations to check.")
            return

        for user in users_with_trello:
            print(f"\n[*] Checking tasks for user: {user.username}")
            client = TrelloClient(
                api_key=TRELLO_API_KEY,
                api_secret=TRELLO_API_SECRET,
                token=user.trello_credentials.token
            )

            # Get all cards created by this user from our database
            tracked_cards = TrelloCard.query.filter_by(user_id=user.id).all()
            if not tracked_cards:
                print("  -> No tracked cards found for this user.")
                continue

            # In a real app, you would let the user define their "Done" list
            # For now, we'll assume any card moved from its original list is progressing.
            for card_record in tracked_cards:
                try:
                    card = client.get_card(card_record.card_id)
                    if card.list_id != card_record.list_id:
                        print(
                            f"  -> STATUS UPDATE: Task '{card.name}' has been moved to a new list '{card.get_list().name}'.")
                    else:
                        print(f"  -> STATUS OK: Task '{card.name}' is still in its original list.")
                except Exception as e:
                    # This can happen if the card was deleted in Trello
                    print(
                        f"  -> ERROR: Could not fetch card ID {card_record.card_id}. It may have been deleted. Error: {e}")


if __name__ == "__main__":
    # For testing, we'll run the job every 1 minute.
    # For production, you would change this to schedule.every().day.at("09:00")
    schedule.every(1).minutes.do(check_trello_tasks)

    print("--- AI Accountability Worker Started ---")
    print("Waiting for scheduled job...")

    while True:
        schedule.run_pending()
        time.sleep(1)
