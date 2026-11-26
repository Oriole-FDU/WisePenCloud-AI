# 读取 CSV 与合并汇总

## 读取 CSV

先确定表头和分隔符，再用 `csv.reader` 或 pandas 读取：

```python
import pandas as pd

sales_path = "input/sales.csv"  # 换成实际路径
# 列名和 dtype 换成实际值
sales = pd.read_csv(sales_path, encoding="utf-8-sig", dtype={"门店编号": str})
```

## 合并与汇总

按两张表共有的键合并，再按某个维度分组、对某个数值列汇总。
读 Excel 用 `sheet_name` 指定工作表、`usecols` 指定列，读 CSV 用 `sep` 指定分隔符；求和前区分明细行与小计行，避免重复统计：

```python
import pandas as pd

sales_path = "input/sales.csv"      # 换成实际路径
stores_path = "input/stores.xlsx"   # 换成实际路径
# `门店编号`、`地区`、`收入` 都是示例列名，列名、工作表名都换成实际值
sales = pd.read_csv(sales_path, encoding="utf-8-sig", dtype={"门店编号": str})
stores = pd.read_excel(stores_path, sheet_name="门店", dtype={"门店编号": str})
joined = sales.merge(stores[["门店编号", "地区"]], on="门店编号", how="left", validate="many_to_one")
print(joined.groupby("地区", dropna=False)["收入"].sum().to_string())
```

回答时引用源文件和字段名。
