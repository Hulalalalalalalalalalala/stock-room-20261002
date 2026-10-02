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
- `update-material` → `StockRoom.update_material(code, name, unit)`，修改已登记物料的名称和单位。三个参数都只接受去除首尾空白后的非空字符串；编码去除空白后区分大小写用于定位物料，不支持改码或新建，名称与单位保留内部空白。返回只含 `code`、`name`、`unit` 的最新资料对象，重复提交相同资料同样成功。启用与停用物料均可修改，原状态保持不变。单位仅在该物料没有任何出入库、盘点或冲销记录时允许改变；存在任一历史记录（含零库存、零差异盘点、已冲销流水）时提交不同单位一律抛出 `ValueError`，单位按去除首尾空白后的字符串精确比较，提交原单位仍可修改名称。修改不重写历史、不新增流水、不占用编号；校验失败时 `data.json` 的字节、库存、最低库存、状态及全部历史保持原样，尚无数据文件时也不创建。资料保存在 `root/data.json` 中，重新打开同一目录仍生效，`stock` 与 `shortages` 显示最新名称与单位。
- `move` → `StockRoom.movement(...)`。参数名见 `core.py` 的公开方法签名。
- `move-batch` → `StockRoom.movement_batch(rows)`，一次提交多笔普通出入库。输入对象含非空列表 `rows`，每行只能含 `code`、`quantity`、`reference` 三个字段；编码与编号去除首尾空白后区分大小写，`quantity` 为非零整数（正入负出，不接受布尔、小数或字符串）。整批按输入顺序计算各物料库存，任一行会使库存变负即抛出 `ValueError` 并拒绝整批，即使后续行入库可以补足也不例外。编号在批内不得重复，也不得与已有流水、盘点（含零差异盘点）或冲销编号冲突。任一行校验失败时不写入、不占用编号，`data.json` 的字节内容、库存、全部历史和最低库存配置保持原样；原先不存在的数据文件也不创建。成功后各行按顺序追加普通流水，返回与输入等长、同序的结果列表，每项含 `code`、`quantity`、`reference` 与 `balance`（该行完成后的该物料库存，而非整批最终库存）；这些流水可沿用 `reverse` 逐笔冲销，不另设批次编号或批次历史。
- `stock` → `StockRoom.stock(...)`。参数名见 `core.py` 的公开方法签名。
- `history` → `StockRoom.history(...)`。参数名见 `core.py` 的公开方法签名。
- `count` → `StockRoom.count(code, counted, reference)`，按单个物料登记盘点。`counted` 为实点数量（非负整数）；返回 `code`、`reference`、`before`（盘点时库存）、`counted`、`difference`（实点减原库存），库存随后等于 `counted`。差异非零时在出入库流水中追加一条同字段记录（`quantity` 为差异、`reference` 为盘点编号）；零差异只登记盘点、不追加流水。
- `counts` → `StockRoom.counts(code)`，按登记顺序返回该物料的盘点对象列表；已登记但无盘点记录的物料返回空列表。后续出入库不改变已保存的盘点数值。
- `count-batch` → `StockRoom.count_batch(rows)`，一次提交多种物料的实点结果共同生效。输入对象含非空列表 `rows`，每行只能含 `code`、`counted`、`reference` 三个字段；编码与编号去除首尾空白后区分大小写（内部空白保留），`counted` 为非负整数（不接受布尔、小数或字符串）。启用与停用物料均可盘点。返回与输入等长、同序的结果列表，每项沿用单物料盘点的 `code`、`reference`、`before`、`counted`、`difference` 五个字段；`before` 取提交前台账值，差异为实点减原库存，完成后库存等于实点数量。成功时按输入顺序追加盘点记录，仅为非零差异追加同编号的调整流水，零差异也占用编号但不追加流水。去除首尾空白后的物料编码在批内不得重复；编号既不能批内重复，也不能与任何物料的流水、盘点或冲销编号冲突。`rows` 为空或非列表、行非对象或字段缺失或多余、字符串或数量不合法、物料不存在、批内物料重复及编号冲突均抛出 `ValueError` 并整批拒绝：不写入、不占用编号，`data.json` 的字节、库存、全部历史、最低库存和状态保持原样，尚无数据文件时不创建，修正后可复用失败请求中的编号。批内盘点与单物料盘点同样锁定后续单位变更，其编号不能被 `reverse` 冲销。
- `reverse` → `StockRoom.reverse(original_reference, reference)`，冲销一笔普通出入库流水。保留原流水，追加一条数量相反、编号为 `reference` 的流水，并返回 `code`、`original_reference`、`reference`、`quantity`（冲销数量）与 `balance`（冲销完成时的库存）。冲销作用于当前库存，不回退其间的其他出入库或盘点，也不改写历史盘点数值。
- `reversals` → `StockRoom.reversals(code)`，按登记顺序返回该物料的冲销对象列表，每项含上述五个字段，`balance` 保留登记时的值；已登记但无冲销的物料返回空列表。
- `set-minimum` → `StockRoom.set_minimum(code, minimum)`，为已登记物料设置最低库存。`minimum` 为非负整数（不接受布尔、小数、字符串或其他类型）；返回 `code` 与 `minimum`。再次设置覆盖原值，设为零即取消预警。设置不新增出入库流水或任何历史记录，也不占用编号。
- `set-active` → `StockRoom.set_active(code, active)`，停用或恢复已登记物料。`active` 只接受布尔值（不接受整数、字符串或其他类型）；返回 `code` 与 `active`。停用不要求库存为零，不改变库存与任何历史；重复设置同一状态成功返回原状态。状态切换不产生流水或历史、不占用编号。停用后普通出入库（`move` 与 `move-batch`，无论数量正负）一律抛出 `ValueError`；`move-batch` 只要包含停用物料即整批拒绝，不保存其他合法行、不占用编号。盘点（`count`）与冲销（`reverse`）仍按既有规则处理停用物料，全部查询入口语义不变。恢复后普通出入库继续遵守已有校验。
- `material-status` → `StockRoom.material_status(code)`，返回 `code` 与 `active`。新登记物料及旧数据中未设置状态的物料均视为启用（`active` 为 `true`）。查询不改写文件，也不为尚无数据的目录创建文件。
- `shortages` → `StockRoom.shortages()`，无参数，可省略输入文件。返回缺料物料对象列表，每项含 `code`、`name`、`unit`、`quantity`（当前台账库存）、`minimum` 与 `shortage`（`minimum` 减 `quantity`）。未设置最低库存的物料按零处理；仅库存严格小于最低库存的物料进入结果，按 `code` 的 Unicode 码点逐字符升序排列。没有缺料或尚未登记物料时返回空列表。查询不改写文件，也不为尚无数据的目录创建文件。

`code` 与盘点 `reference` 只接受去除首尾空白后的非空字符串，编码区分大小写；`counted` 必须是非负整数（不接受布尔、小数或其他类型），否则抛出 `ValueError`。盘点编号与全部物料的出入库编号共用唯一范围，重复编号抛出 `ValueError`；普通出入库也不能复用零差异盘点占用的编号。校验失败时 `data.json`、库存及两类历史均保持不变。

冲销的 `original_reference` 与 `reference` 同样只接受去除首尾空白后的非空字符串并区分大小写。原编号不存在、指向盘点（含零差异盘点）或冲销记录、或该笔流水已被成功冲销，新编号与任一物料的流水或盘点编号重复，以及冲销后库存为负，均抛出 `ValueError`；查询未知物料的冲销列表也抛出 `ValueError`。每笔普通流水最多成功冲销一次。拒绝操作后 `data.json`、库存和全部历史保持不变，不新增编号占用。冲销关联保存在 `root/data.json` 中，重新打开同一目录仍可查询。

最低库存的 `code` 同样去除首尾空白后匹配并区分大小写；编码不是非空字符串、物料不存在或 `minimum` 不是非负整数时抛出 `ValueError`，此时 `data.json` 与全部业务查询结果保持不变，不占用流水编号。最低库存保存在 `root/data.json` 中，重新打开同一目录仍生效；旧数据目录无需补写配置即可直接查询缺料清单，后续出入库、盘点和冲销按最新库存影响清单。

停用状态的 `code` 同样去除首尾空白后匹配并区分大小写；编码不是非空字符串、物料不存在或 `active` 不是布尔值时抛出 `ValueError`，此时 `data.json` 的字节、库存、状态、最低库存配置及全部历史保持原样，不占用流水编号。停用物料的普通出入库无论数量正负均抛出 `ValueError`；批量出入库遇到停用物料时整批拒绝，其他合法行也不保存。状态保存在 `root/data.json` 中，重新打开同一目录仍生效，状态查询不写文件。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前仅支持一个仓库及整数数量（盘点实点数量同样为非负整数）。没有库位、采购单和多进程并发控制。不承诺并发写入或断电恢复。
