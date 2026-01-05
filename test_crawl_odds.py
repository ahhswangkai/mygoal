
import logging
from crawler import FootballCrawler
from db_storage import MongoDBStorage
import json

def test_crawl_odds():
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    crawler = FootballCrawler()
    
    # Use a recent match ID that likely has odds data
    # We can fetch one from the daily list first
    import datetime
    target_date = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
    logger.info(f"Fetching matches for {target_date}...")
    
    matches = crawler.crawl_daily_matches(target_date, fetch_odds=False)
    
    if not matches:
        logger.error("No matches found.")
        return
        
    match = matches[0]
    match_id = match.get('match_id')
    logger.info(f"Testing match ID: {match_id}")
    
    if not match_id:
        logger.error("No match ID found.")
        return

    # Test update_single_match_odds
    success = crawler.update_single_match_odds(match)
    
    if success:
        logger.info("Successfully updated match odds.")
        logger.info(f"Match Data Keys: {match.keys()}")
        
        # Check specific keys
        asian_keys = [k for k in match.keys() if 'asian' in k]
        ou_keys = [k for k in match.keys() if 'ou_' in k]
        hi_keys = [k for k in match.keys() if 'hi_' in k]
        
        logger.info(f"Asian Handicap Keys: {asian_keys}")
        logger.info(f"Over/Under Keys: {ou_keys}")
        logger.info(f"Handicap Index Keys: {hi_keys}")
        
        print("\n--- Asian Handicap ---")
        for k in asian_keys:
            print(f"{k}: {match[k]}")
            
        print("\n--- Over/Under ---")
        for k in ou_keys:
            print(f"{k}: {match[k]}")
            
        print("\n--- Handicap Index ---")
        for k in hi_keys:
            print(f"{k}: {match[k]}")
            
    else:
        logger.error("Failed to update match odds.")

if __name__ == "__main__":
    test_crawl_odds()
