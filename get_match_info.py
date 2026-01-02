from db_storage import MongoDBStorage
import json
from datetime import datetime

class DateTimeEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

def get_match_details(match_id):
    storage = MongoDBStorage()
    # Use find_one or get_matches with a filter
    match = storage.db.matches.find_one({'match_id': str(match_id)})
    
    if match:
        # Remove _id for printing
        if '_id' in match:
            del match['_id']
        print(json.dumps(match, indent=2, cls=DateTimeEncoder, ensure_ascii=False))
    else:
        print(f"Match ID {match_id} not found in database.")

if __name__ == "__main__":
    get_match_details(1216146)


