"""审计器自测：用一份干净的分镜当基线，逐项改坏，确认每一项检查都能抓到。"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify  # noqa: E402

SOURCE = """
每天咖啡因别超过 400 毫克，大约 4 杯煮咖啡。一大杯美式约 150 毫克。
半衰期约 5 小时。睡前 6 小时喝 400 毫克，睡眠减少 1 小时以上。无因咖啡也有 2 到 15 毫克。
"""

GOOD = {
    "title": "咖啡怎么喝才不失眠",
    "hook": "下午那杯咖啡，晚上还在值班",
    "shots": [
        {"no": 1, "visual": "办公桌上的咖啡杯", "narration": "每天咖啡因别超过 400 毫克，大约 4 杯煮咖啡。", "seconds": 6},
        {"no": 2, "visual": "大杯美式特写", "narration": "一大杯美式，咖啡因大约 150 毫克。", "seconds": 4},
        {"no": 3, "visual": "时钟转动", "narration": "咖啡因的半衰期约 5 小时，下午喝的，晚上还剩一半。", "seconds": 6},
        {"no": 4, "visual": "卧室熄灯", "narration": "睡前 6 小时喝 400 毫克，睡眠会减少 1 小时以上。", "seconds": 6},
        {"no": 5, "visual": "无因咖啡包装", "narration": "无因咖啡也不是零咖啡因，还有 2 到 15 毫克。", "seconds": 6},
        {"no": 6, "visual": "日程表", "narration": "早上正常喝，下午看看额度，傍晚悠着点。", "seconds": 6},
        {"no": 7, "visual": "主播面对镜头", "narration": "咖啡是好工具，但好工具也要看说明书。", "seconds": 6},
        {"no": 8, "visual": "片尾字幕", "narration": "你一般几点喝最后一杯？评论区聊聊。", "seconds": 6},
        {"no": 9, "visual": "黑场", "narration": "记住这几个数字，下次点单心里有数。", "seconds": 6},
        {"no": 10, "visual": "片尾 logo", "narration": "关注我，下期继续讲程序员的健康账。", "seconds": 8},
    ],
    "total_seconds": 60,
    "bgm": "轻快理性",
    "cta": "评论区聊聊你的最后一杯",
}


def levels(result, check):
    return [f["level"] for f in result["findings"] if f["check"] == check]


class VerifyTests(unittest.TestCase):
    def test_clean_baseline_passes(self):
        result = verify.audit(GOOD, SOURCE)
        self.assertTrue(result["passed"], json.dumps(result["findings"], ensure_ascii=False))
        self.assertEqual(result["fails"], 0)

    def test_fabricated_number_is_caught(self):
        bad = copy.deepcopy(GOOD)
        bad["shots"][1]["narration"] = "一大杯美式，咖啡因大约 180 毫克。"
        result = verify.audit(bad, SOURCE)
        self.assertFalse(result["passed"])
        self.assertIn("FAIL", levels(result, "numbers"))
        missing = [f for f in result["findings"] if f["check"] == "numbers"][0]["details"]["missing"]
        self.assertEqual(missing[0]["number"], "180")

    def test_number_in_hook_is_also_traced(self):
        bad = copy.deepcopy(GOOD)
        bad["hook"] = "90% 的人都喝错了咖啡"
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "numbers"))

    def test_duration_mismatch_is_caught(self):
        bad = copy.deepcopy(GOOD)
        bad["shots"][0]["seconds"] = 5
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "duration"))

    def test_declared_total_must_be_60(self):
        bad = copy.deepcopy(GOOD)
        bad["total_seconds"] = 59
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "duration"))

    def test_too_fast_narration_is_caught(self):
        bad = copy.deepcopy(GOOD)
        bad["shots"][0]["narration"] = ("每天咖啡因别超过 400 毫克，大约 4 杯煮咖啡，这句话实在太长了，"
                                        "六秒钟根本念不完，主播念到一半就得喘气，观众也听不清楚。")  # 55 字 / 6 秒 ≈ 9.2
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "speed"))

    def test_slightly_fast_is_only_warning(self):
        bad = copy.deepcopy(GOOD)
        bad["shots"][7]["narration"] = "你一般几点喝最后一杯咖啡呢？来评论区聊一聊吧。"  # 24 字 / 6 秒 = 6.0
        result = verify.audit(bad, SOURCE)
        self.assertIn("WARN", levels(result, "speed"))
        self.assertTrue(result["passed"])

    def test_too_few_datapoints_is_caught(self):
        bad = copy.deepcopy(GOOD)
        for shot in bad["shots"]:
            shot["narration"] = "喝咖啡这件事，其实很讲究时间。"
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "datapoints"))

    def test_missing_field_is_caught(self):
        bad = copy.deepcopy(GOOD)
        del bad["cta"]
        result = verify.audit(bad, SOURCE)
        self.assertIn("FAIL", levels(result, "schema"))

    def test_parse_fenced_response(self):
        raw = "好的，脚本如下：\n```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```\n以上。"
        parsed = verify.parse_response_text(raw)
        self.assertEqual(parsed["total_seconds"], 60)

    def test_number_normalization(self):
        self.assertEqual(verify.normalize_number("5.0"), "5")
        self.assertEqual(verify.normalize_number("05"), "5")
        self.assertEqual(verify.normalize_number("1.40"), "1.4")
        self.assertEqual(verify.normalize_number("2013"), "2013")

    def test_long_number_counts_spoken_syllables(self):
        self.assertEqual(verify.speakable_count("2147483647"), 18)  # 二十一亿四千七百四十八万三千六百四十七
        self.assertEqual(verify.speakable_count("2038年"), 5)        # 年份仍按 1 字/位
        self.assertEqual(verify.speakable_count("400 毫克"), 5)

    def test_chinese_numerals_trigger_manual_check_warning(self):
        bad = copy.deepcopy(GOOD)
        bad["shots"][0]["narration"] = "每天咖啡因别超过四百毫克，大约四杯煮咖啡。"
        result = verify.audit(bad, SOURCE)
        self.assertIn("WARN", levels(result, "cn_numerals"))
        hits = [f for f in result["findings"] if f["check"] == "cn_numerals"][0]["details"]["hits"]
        self.assertEqual(hits[0]["text"], "四百")

    def test_clean_baseline_has_no_chinese_numerals(self):
        result = verify.audit(GOOD, SOURCE)
        self.assertEqual(levels(result, "cn_numerals"), ["PASS"])


if __name__ == "__main__":
    unittest.main()
