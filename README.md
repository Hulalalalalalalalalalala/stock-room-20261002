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
- `reverse` → `StockRoom.reverse(original_reference, reference)`，冲销一笔普通出入库流水。保留原流水，追加一条数量相反、编号为 `reference` 的流水，并返回 `code`、`original_reference`、`reference`、`quantity`（冲销数量）与 `balance`（冲销完成时的库存）。冲销作用于当前库存，不回退其间的其他出入库或盘点，也不改写历史盘点数值。
- `reversals` → `StockRoom.reversals(code)`，按登记顺序返回该物料的冲销对象列表，每项含上述五个字段，`balance` 保留登记时的值；已登记但无冲销的物料返回空列表。
- `set-minimum` → `StockRoom.set_minimum(code, minimum)`，为已登记物料设置最低库存。`minimum` 为非负整数（不接受布尔、小数、字符串或其他类型）；返回 `code` 与 `minimum`。再次设置覆盖原值，设为零即取消预警。设置不新增出入库流水或任何历史记录，也不占用编号。
- `shortages` → `StockRoom.shortages()`，无参数，可省略输入文件。返回缺料物料对象列表，每项含 `code`、`name`、`unit`、`quantity`（当前台账库存）、`minimum` 与 `shortage`（`minimum` 减 `quantity`）。未设置最低库存的物料按零处理；仅库存严格小于最低库存的物料进入结果，按 `code` 的 Unicode 码点逐字符升序排列。没有缺料或尚未登记物料时返回空列表。查询不改写文件，也不为尚无数据的目录创建文件。

`code` 与盘点 `reference` 只接受去除首尾空白后的非空字符串，编码区分大小写；`counted` 必须是非负整数（不接受布尔、小数或其他类型），否则抛出 `ValueError`。盘点编号与全部物料的出入库编号共用唯一范围，重复编号抛出 `ValueError`；普通出入库也不能复用零差异盘点占用的编号。校验失败时 `data.json`、库存及两类历史均保持不变。

冲销的 `original_reference` 与 `reference` 同样只接受去除首尾空白后的非空字符串并区分大小写。原编号不存在、指向盘点（含零差异盘点）或冲销记录、或该笔流水已被成功冲销，新编号与任一物料的流水或盘点编号重复，以及冲销后库存为负，均抛出 `ValueError`；查询未知物料的冲销列表也抛出 `ValueError`。每笔普通流水最多成功冲销一次。拒绝操作后 `data.json`、库存和全部历史保持不变，不新增编号占用。冲销关联保存在 `root/data.json` 中，重新打开同一目录仍可查询。

最低库存的 `code` 同样去除首尾空白后匹配并区分大小写；编码不是非空字符串、物料不存在或 `minimum` 不是非负整数时抛出 `ValueError`，此时 `data.json` 与全部业务查询结果保持不变，不占用流水编号。最低库存保存在 `root/data.json` 中，重新打开同一目录仍生效；旧数据目录无需补写配置即可直接查询缺料清单，后续出入库、盘点和冲销按最新库存影响清单。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前仅支持一个仓库及整数数量（盘点实点数量同样为非负整数）。没有库位、采购单和多进程并发控制。不承诺并发写入或断电恢复。
