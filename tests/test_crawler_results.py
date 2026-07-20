import unittest

from crawler import FootballCrawler


class CrawlerResultTests(unittest.TestCase):
    def test_json_match_parser_keeps_half_time_score(self):
        crawler = FootballCrawler()
        matches = crawler.parse_match_list_json({
            "data": {
                "matches": [{
                    "fid": "1362707",
                    "order": "周日205",
                    "simpleleague": "瑞典超",
                    "matchround": "第13轮",
                    "matchtime": "2026-07-19 22:30:00",
                    "status": "4",
                    "status_desc": "完场",
                    "homescore": "4",
                    "awayscore": "0",
                    "homehalfscore": "1",
                    "awayhalfscore": "0",
                    "homesxname": "哈马比",
                    "awaysxname": "代格福什",
                    "ownerdate": "2026-07-19",
                }]
            }
        })

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["score"], "4-0")
        self.assertEqual(matches[0]["home_half_score"], "1")
        self.assertEqual(matches[0]["away_half_score"], "0")
        self.assertEqual(matches[0]["half_score"], "1-0")


if __name__ == "__main__":
    unittest.main()
