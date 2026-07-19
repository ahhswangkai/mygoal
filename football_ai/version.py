"""Football AI Engine version and default calibration profile."""

ENGINE_NAME = "Football AI Engine"
ENGINE_CODE = "FAE"
ENGINE_VERSION = "2.3.0"
SCHEMA_VERSION = "2.2"

DIMENSION_WEIGHTS = {
    "handicap": 0.20,
    "euro": 0.18,
    "over_under": 0.12,
    "sporttery": 0.16,
    "motivation": 0.10,
    "injuries": 0.08,
    "history": 0.06,
    "form": 0.10,
}

DEFAULT_RULE_WEIGHTS = {
    "euro-home-support": 1.00,
    "euro-away-support": 1.00,
    "euro-draw-support": 0.90,
    "asian-line-home": 1.00,
    "asian-line-away": 1.00,
    "asian-home-water": 0.85,
    "asian-away-water": 0.85,
    "market-consensus-home": 1.10,
    "market-consensus-away": 1.10,
    "recent-form-home": 0.90,
    "recent-form-away": 0.90,
    "history-home": 0.65,
    "history-away": 0.65,
    "total-over": 0.90,
    "total-under": 0.90,
    "hot-overheat": 1.00,
    "handicap-drop": 1.00,
    "euro-asian-divergence": 1.00,
    "market-data-anomaly": 1.00,
    "deep-high-water": 1.00,
    "cup-variance": 1.00,
    "data-quality": 1.00,
}

VERSION_MANIFEST = {
    "name": ENGINE_NAME,
    "code": ENGINE_CODE,
    "version": ENGINE_VERSION,
    "schema_version": SCHEMA_VERSION,
    "features": [
        "mygoal 赔率与盘口归一化",
        "盘口类型 A-G 分类",
        "八维评分、星级与概率输出",
        "固定推荐、每日排名和危险盘口",
        "赛后复盘与规则权重自动调节",
        "版本化 Skill 候选、历史验证、发布与回滚",
        "基本面扩展接口：排名、近期、交锋、赛程、伤停、首发、天气",
        "跨场星级校准、欧亚背离与异常数据自动降级",
        "主选/防选分层与未校准概率口径标识",
        "玩法级赔率价值指数、投注分与市场去水概率",
        "盘口可信度评分及不下注决策层",
    ],
    "learning_policy": {
        "minimum_samples": 10,
        "decrease_below": 0.60,
        "increase_above": 0.80,
        "step": 0.05,
        "minimum_weight": 0.70,
        "maximum_weight": 1.30,
        "release_mode": "staged",
        "minimum_new_samples": 10,
    },
}
