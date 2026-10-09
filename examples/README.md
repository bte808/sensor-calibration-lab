# 合成温度—电压标定示例

`synthetic_temperature.csv` 是程序生成的**合成数据**，没有连接传感器、采集卡或其他硬件，也没有开展真实温度实验。它用于展示标定流程，不用于声称某种传感器具有特定性能。

数据有 6 档参考温度，每档 3 次：

| 列 | 含义 | 单位 |
| --- | --- | --- |
| `reference_temperature_c` | 生成公式中的参考温度 | °C |
| `sensor_output_v` | 生成公式中的传感器输出 | V |
| `repeat` | 同一参考档位内的合成重复序号，1–3 | 无 |

## 公开生成规则

```text
参考温度 T = [0, 10, 20, 30, 40, 50] °C
各档固定偏差 b = [0.000, 0.002, -0.003, 0.004, -0.002, 0.003] V
重复偏差 r = [-0.004, 0.001, 0.003] V

输出 V(T, j) = 0.500 V + (0.020 V/°C) × T + b[T 所在档位] + r[j]
```

每一档使用相同的重复偏差数组。固定档位偏差让数据不会恰好落在一条直线上，固定重复偏差让重复性可见。它们都是人为选择的数值，不是对真实误差分布或环境影响的估计。

生成器使用 Python 标准库 `decimal` 精确进行十进制计算，输出 6 位小数、UTF-8 编码和 LF 换行，不使用随机数。

在项目根目录运行。macOS / Linux：

```sh
# 重新生成数据
python3 examples/generate_example.py

# 校验文件与公开公式逐字节一致，不修改文件
python3 examples/generate_example.py --check
```

Windows PowerShell：

```powershell
py -3 examples/generate_example.py
py -3 examples/generate_example.py --check
```

若没有 `py`，但 `python --version` 显示 3.9+，可将 `py -3` 替换为 `python`。

理论上，每一档的输出样本标准差都为 `√0.000013 V ≈ 0.00360555 V`；合并重复性标准差也相同，共有 `6 × (3 − 1) = 12` 个自由度。这个精确一致性来自合成规则，不应期待真实实验具有同样的模式。

拟合应使用全部 18 个观测，参考列选 `reference_temperature_c`，输出列选 `sensor_output_v`。页面中的单位只是标签，不会转换数据。
