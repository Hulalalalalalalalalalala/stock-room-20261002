# 仓库物料台账

登记物料、记录正负数量的出入库流水，并从流水计算当前库存；支持单物料盘点登记与盘点记录查询。

## 运行

需要 Python 3.10 或更新版本，仅使用标准库，无依赖安装步骤。请在本目录运行：

```sh
python3 -m stock_room --root ./state demo
python3 -m unittest discover -s tests -v
```

这是本地命令行程序，不监听网络端口，无账户或密码。`demo` 在指定 root 的临时子目录中读取 examples 样例并演示业务，结束后清理样例状态，不改变现有数据。

## 正常使用

公开 API：`from stock_room import StockRoom`，然后 `StockRoom(root)`。每个命令接收可选的 JSON 文件，其对象键与 API 方法参数一致。例如：

```sh
python3 -m stock_room --root ./state register examples/materials.json
```

JSON 数组会按顺序执行多个独立操作；先前成功操作保留，后续失败不会回滚整批。重跑登记命令遇到已存在的标识会报错。

- `register` → `StockRoom.register(...)`。参数名见 `core.py` 的公开方法签名。
- `move` → `StockRoom.movement(...)`。参数名见 `core.py` 的公开方法签名。
- `stock` → `StockRoom.stock(...)`。参数名见 `core.py` 的公开方法签名。
- `history` → `StockRoom.history(...)`。参数名见 `core.py` 的公开方法签名。
- `count` → `StockRoom.count(...)`。提交 `code`、`counted`（实点数量，非负整数）与 `reference`；返回含 `before`、`counted`、`difference` 的对象，差异非零时追加一条对应流水，库存随后等于 `counted`。
- `counts` → `StockRoom.counts(...)`。按 `code` 返回盘点记录列表，按登记顺序排列。

盘点编号与全部物料的出入库编号共用唯一范围，重复编号（包括零差异盘点占用的编号）会被拒绝。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前仅支持一个仓库及整数数量。没有库位、采购单和多进程并发控制。 不承诺并发写入或断电恢复。
