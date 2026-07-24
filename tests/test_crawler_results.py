import unittest

from crawler import FootballCrawler, is_pregame_match


class CrawlerResultTests(unittest.TestCase):
    def test_only_status_zero_can_update_pregame_odds(self):
        self.assertTrue(is_pregame_match({"status": 0}))
        self.assertTrue(is_pregame_match({"status": "0"}))
        self.assertFalse(is_pregame_match({"status": 1}))
        self.assertFalse(is_pregame_match({"status": "1"}))
        self.assertFalse(is_pregame_match({"status": 2}))
        self.assertFalse(is_pregame_match({}))

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

    def test_analysis_parser_keeps_away_form_and_expected_lineup_separate(self):
        html = """
        <html><head><title>主队VS客队-数据分析-500彩票网</title></head>
        <body><div class="odds_content">
          <div class="M_box">
            <div class="M_title"><h4>赛前联赛积分排名</h4></div>
            <div class="M_sub_title">
              <div class="team_name">主队<span>[联赛2]</span></div>
              <div class="team_name">客队<span>[联赛5]</span></div>
            </div>
            <div class="M_content">
              <div class="team_a"><table class="pub_table">
                <tr><th></th><th>比赛</th><th>胜</th><th>平</th><th>负</th><th>进</th><th>失</th><th>净</th><th>积分</th><th>排名</th><th>胜率</th></tr>
                <tr><td>总成绩</td><td>10</td><td>6</td><td>2</td><td>2</td><td>18</td><td>8</td><td>10</td><td>20</td><td>2</td><td>60%</td></tr>
              </table></div>
              <div class="team_b"><table class="pub_table">
                <tr><th></th><th>比赛</th><th>胜</th><th>平</th><th>负</th><th>进</th><th>失</th><th>净</th><th>积分</th><th>排名</th><th>胜率</th></tr>
                <tr><td>总成绩</td><td>10</td><td>4</td><td>2</td><td>4</td><td>12</td><td>12</td><td>0</td><td>14</td><td>5</td><td>40%</td></tr>
              </table></div>
            </div>
          </div>
          <div class="M_box record">
            <div class="M_title"><h4>近期战绩</h4></div>
            <div class="odds_zj_tubiao module_cur">
              <div class="team_a"><table class="pub_table">
                <tr><th>赛事</th><th>日期</th><th>比赛</th><th>盘口</th><th>半场</th><th>赛果</th><th>盘路</th><th>大小</th></tr>
                <tr><td>联赛</td><td>26-07-10</td><td class="dz"><span class="dz-l">主队</span><em>2:0</em><span class="dz-r">甲队</span></td><td title="半球">0.5</td><td>1:0</td><td>胜</td><td>赢</td><td>小</td></tr>
              </table></div>
              <div class="team_b"><table class="pub_table">
                <tr><th>赛事</th><th>日期</th><th>比赛</th><th>盘口</th><th>半场</th><th>赛果</th><th>盘路</th><th>大小</th></tr>
                <tr><td>联赛</td><td>26-07-11</td><td class="dz"><span class="dz-l">乙队</span><em>1:2</em><span class="dz-r">客队</span></td><td title="受半球">0.5</td><td>0:1</td><td>胜</td><td>赢</td><td>大</td></tr>
              </table></div>
            </div>
          </div>
          <div class="M_box starting">
            <div class="M_title"><h4>主队VS客队 预计阵容</h4></div>
            <div class="M_content">
              <div class="team_a"><div class="team_name">主队阵型:4-4-2</div><table class="pub_table">
                <tr><th>- 首发 -</th><th>- 替补 -</th></tr>
                <tr><td><span class="td_sp3">1</span>主门将(守门员)</td><td><span class="td_sp3">12</span>主替补(守门员)</td></tr>
                <tr><th>- 伤病 -</th><th>- 停赛 -</th></tr>
                <tr><td><span class="td_sp3"></span></td><td><span class="td_sp3"></span></td></tr>
              </table></div>
              <div class="team_b"><div class="team_name">客队阵型:4-3-3</div><table class="pub_table">
                <tr><th>- 首发 -</th><th>- 替补 -</th></tr>
                <tr><td><span class="td_sp3">9</span>客前锋(前锋)</td><td><span class="td_sp3">19</span>客替补(前锋)</td></tr>
                <tr><th>- 伤病 -</th><th>- 停赛 -</th></tr>
                <tr><td><span class="td_sp3"></span></td><td><span class="td_sp3"></span></td></tr>
              </table></div>
            </div>
          </div>
        </div></body></html>
        """

        class Response:
            apparent_encoding = "utf-8"
            encoding = "utf-8"
            text = html

        crawler = FootballCrawler()
        crawler._make_request = lambda _url: Response()
        result = crawler.crawl_match_analysis("1")

        self.assertEqual(result["teams"], ["主队", "客队"])
        self.assertEqual(result["recent"]["home"][0]["home_team"], "主队")
        self.assertEqual(result["recent"]["away"][0]["away_team"], "客队")
        self.assertEqual(result["team_rankings"]["away"]["records"][0]["rank"], "5")
        self.assertEqual(result["lineups"]["status"], "predicted")
        self.assertEqual(result["lineups"]["away"]["starters"][0]["name"], "客前锋")
        self.assertEqual(result["injuries"]["status"], "no_listed_players")


if __name__ == "__main__":
    unittest.main()
