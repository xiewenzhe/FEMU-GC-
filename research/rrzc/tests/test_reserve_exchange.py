"""预留交换的合成日志回归，不代表真实设备结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_reserve_exchange import inspect

class ExchangeLogTest(unittest.TestCase):
    LINE='公共预留归属交换：需要字节=67108864，旧Zone=805306368，旧剩余字节=8388608，旧有效字节=251658240，新Zone=1073741824，新剩余字节=209715200，新有效字节=41943040，不复制=1，不Finish=1，不Reset=1，普通活跃令牌转移=1'
    def test_valid(self):self.assertEqual(inspect([self.LINE])['交换次数'],1)
    def test_insufficient(self):
        with self.assertRaises(ValueError):inspect([self.LINE.replace('新剩余字节=209715200','新剩余字节=41943040')])
    def test_wrong_effect(self):
        with self.assertRaises(ValueError):inspect([self.LINE.replace('不Reset=1','不Reset=0')])
    def test_same_zone(self):
        with self.assertRaises(ValueError):inspect([self.LINE.replace('新Zone=1073741824','新Zone=805306368')])

if __name__=='__main__':unittest.main()
