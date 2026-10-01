# 仓库物料台账

登记物料、记录正负数量的出入库流水与单物料盘点，并从流水计算当前库存。

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
- `count` → `StockRoom.count(code, counted, reference)`，按单个物料登记盘点。`counted` 为实点数量（非负整数）；返回 `code`、`reference`、`before`（盘点时库存）、`counted`、`difference`（实点减原库存），库存随后等于 `counted`。差异非零时在出入库流水中追加一条同字段记录（`quantity` 为差异、`reference` 为盘点编号）；零差异只登记盘点、不追加流水。
- `counts` → `StockRoom.counts(code)`，按登记顺序返回该物料的盘点对象列表；已登记但无盘点记录的物料返回空列表。后续出入库不改变已保存的盘点数值。

`code` 与盘点 `reference` 只接受去除首尾空白后的非空字符串，编码区分大小写；`counted` 必须是非负整数（不接受布尔、小数或其他类型），否则抛出 `ValueError`。盘点编号与全部物料的出入库编号共用唯一范围，重复编号抛出 `ValueError`；普通出入库也不能复用零差异盘点占用的编号。校验失败时 `data.json`、库存及两类历史均保持不变。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前仅支持一个仓库及整数数量（盘点实点数量同样为非负整数）。没有库位、采购单和多进程并发控制。不承诺并发写入或断电恢复。
