#!/usr/bin/env python3
"""生成明确标记的、可复现的合成温度—电压标定示例。仅使用标准库。"""

import argparse
import csv
import io
import sys
from decimal import Decimal
from pathlib import Path


REFERENCE_LEVELS_C = (0, 10, 20, 30, 40, 50)
LEVEL_BIASES_V = ("0.000", "0.002", "-0.003", "0.004", "-0.002", "0.003")
REPEAT_OFFSETS_V = ("-0.004", "0.001", "0.003")
OUTPUT_PATH = Path(__file__).resolve().with_name("synthetic_temperature.csv")


def generate_csv():
    """返回固定公式生成的 UTF-8/LF CSV 文本，不访问硬件或使用随机数。"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("reference_temperature_c", "sensor_output_v", "repeat"))
    for temperature, bias in zip(REFERENCE_LEVELS_C, LEVEL_BIASES_V):
        for repeat, offset in enumerate(REPEAT_OFFSETS_V, start=1):
            output = (
                Decimal("0.500")
                + Decimal("0.020") * Decimal(temperature)
                + Decimal(bias)
                + Decimal(offset)
            )
            writer.writerow((temperature, format(output, ".6f"), repeat))
    return buffer.getvalue()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查现有 CSV 与公式一致，不写文件")
    args = parser.parse_args()
    expected = generate_csv().encode("utf-8")
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_bytes() != expected:
            parser.exit(1, "合成示例缺失或与公开生成公式不一致，请重新生成。\n")
        print("合成示例检查通过：18 个观测，6 档参考温度，每档 3 次；字节完全一致。")
        return
    OUTPUT_PATH.write_bytes(expected)
    print("已生成合成示例：{}（不是硬件测量数据）".format(OUTPUT_PATH))


if __name__ == "__main__":
    main()
