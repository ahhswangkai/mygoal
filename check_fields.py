from db_storage import MongoDBStorage

def check_match_fields():
    storage = MongoDBStorage()
    matches = storage.get_matches({'league': '英超', 'status': 2})
    if matches:
        print("Sample match keys:", matches[0].keys())
        if 'home_half_score' in matches[0]:
            print("Half time scores found.")
        else:
            print("No explicit half time scores found in top level keys. Checking details...")
            
        # Print a sample match to see structure
        print(matches[0])

if __name__ == "__main__":
    check_match_fields()


