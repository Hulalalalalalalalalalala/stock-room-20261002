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
- `import-materials-csv` → `StockRoom.import_materials_csv(content)`，整份导入 CSV 物料目录，只登记新物料，不更新已有资料。输入对象含字符串 `content`，即整份 CSV 文本；兼容中文、开头一个可选 BOM 以及 LF、CRLF 换行。表头恰好含 `code`、`name`、`unit` 三列，顺序可变，列名原文匹配，不接受额外空白、重复、缺列或多列。按严格 CSV 格式处理逗号分隔、双引号包裹、引号内换行及连续双引号转义。数据区域的空行忽略，仅含空白或分隔符的记录仍要校验；每条记录恰有三列，各字段去除首尾空白后非空，内部空白保留，编码区分大小写。只有表头时返回空列表且不写文件；空文本、非字符串 `content`、非法表头或引号、列数错误和空字段都抛出 `ValueError`。整份 CSV 内去除首尾空白后的编码重复，或与任一已登记物料编码相同（即使资料完全相同或该物料已停用），都抛出 `ValueError`；单份 CSV 的任何校验失败均不新增物料，`data.json` 的原有字节、库存、状态、最低库存和全部历史保持原样，没有数据文件时也不创建。成功时整份共同生效，返回按数据记录顺序排列的列表，每项仅含 `code`、`name`、`unit`；新物料默认启用、库存为零、最低库存为零，不产生流水、盘点或冲销记录，保存在 `root/data.json` 中，重新打开同一目录后仍可查询和办理出入库。JSON 数组中的多份 CSV 仍逐份独立提交，后续失败保留先前成功。
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
- `create-purchase` → `StockRoom.create_purchase(reference, supplier, rows)`，登记一张采购单。`reference` 与 `supplier` 均为去除首尾空白后的非空字符串（内部空白保留，编号区分大小写）；`rows` 为非空列表，每行只含 `code`、`quantity` 两个字段，`quantity` 为正整数（不接受布尔、小数或字符串）。物料未知或已停用、规范化后物料编码在单内重复、编号与已有采购单重复，以及 `rows` 为空或非列表、行非对象或字段缺失多余、字符串或数量非法，均抛出 `ValueError`；校验失败时不写入、不占用采购编号，`data.json` 原有字节保持原样，尚无文件时不创建。采购编号仅在采购单之间唯一，与流水、盘点或冲销编号相同仍允许。成功时返回仅含 `reference`、`supplier`、`status`、`rows` 的对象，`status` 为 `open`，行按输入顺序含 `code`、`name`、`unit`、`quantity`，名称与单位为登记时快照，后续修改物料资料不影响单据。登记不改变库存、最低库存、物料状态或任何历史，也不占用出入库编号；单据保存在 `root/data.json` 中，重新打开同一目录仍生效。
- `purchase-order` → `StockRoom.purchase_order(reference)`，按编号查询采购单，返回与登记时相同结构的对象（含当前状态）。编号不合法或不存在抛出 `ValueError`；旧数据缺少采购单时视为无记录，查询不补写文件。
- `cancel-purchase` → `StockRoom.cancel_purchase(reference)`，将采购单状态改为 `cancelled` 并返回该对象；重复取消返回原状态对象，不受物料状态影响。编号不合法或不存在抛出 `ValueError`。取消不撤销已收库存或收货记录，不改变库存、最低库存、物料状态或任何历史，也不占用出入库编号。
- `update-purchase` → `StockRoom.update_purchase(reference, supplier, rows)`，完整替换指定采购单的供应商与明细行，采购编号保持不变。`reference` 与 `supplier` 均为去除首尾空白后的非空字符串（内部空白保留，编号区分大小写）；`rows` 为非空列表，每行只含 `code`、`quantity` 两个字段，`quantity` 为正整数（不接受布尔、小数或字符串），允许增删物料、调整数量与重排行顺序。仅允许 `open` 且没有任何关联收货记录的单据修改；一旦收过货，即使全部退货或库存归零也不再允许，普通出入库、盘点或冲销不单独锁定采购单。目标单据未知、已取消或已有收货，以及字符串非法、列表或行结构非法、数量非法、物料未知或停用、规范化后编码重复，均抛出 `ValueError` 并拒绝整次修改：不写入，`data.json` 原有字节与全部业务数据保持原样，尚无文件时不创建；旧数据缺少收货记录按未收货处理，缺少采购单按未知处理。成功时状态仍为 `open`，每行名称与单位取修改时的物料资料作为新快照，此后修改物料资料不影响快照；满足修改条件时重复提交相同输入也成功。返回与 `purchase-order` 相同结构的最新单据，保存在 `root/data.json` 中，重新打开同一目录仍生效；采购列表按新供应商筛选，进度查询使用新行顺序与订购量，未收量等于新订购量，进度为 `pending`，后续收货按新明细校验。修改不改变库存、最低库存、物料状态、其他采购单或既有出入库、盘点、冲销、收货和退货历史，也不占用出入库编号。
- `receive-purchase` → `StockRoom.receive_purchase(purchase_reference, rows)`，按采购单分批收货，整批共同生效。`purchase_reference` 为去除首尾空白后的非空字符串（内部空白保留，区分大小写）；`rows` 为非空列表，每行只含 `code`、`quantity`、`reference` 三个字段，编码与编号同样去除首尾空白后区分大小写（内部空白保留），`quantity` 为正整数（不接受布尔、小数或字符串）。采购单未知或已取消，`rows` 为空或非列表，行非对象或字段缺失多余，字符串或数量非法，物料未知、停用、未列入该采购单或当前单位与采购快照不同，批内编码重复，以及同一物料本次与历次关联收货的累计数量超过采购订购量，均抛出 `ValueError` 并整批拒绝：不写入、不占用编号，`data.json` 的字节、库存、采购单、快照及全部历史保持原样，尚无文件时不创建，修正后可复用失败请求中的编号。收货编号既不能批内重复，也不能与已有出入库流水、盘点或冲销编号冲突；采购编号仍只在采购单之间唯一，与收货编号互不占用。成功时各行按顺序追加入库流水（库存与 `history` 可见），并在单独的收货关联中按采购单保存收货记录，但不改变采购单结构、名称单位快照或状态；返回与输入等长、同序的结果列表，每项仅含 `code`、`quantity`、`reference` 与 `balance`（该行入库后的该物料库存）。已收数量只累计关联收货，不随其他出入库、盘点或冲销变化。收货流水不能被 `reverse` 冲销（抛出 `ValueError`），普通流水仍按原规则冲销。收货关联保存在 `root/data.json` 中，重新打开同一目录后限量校验与记录仍有效。
- `purchase-receipts` → `StockRoom.purchase_receipts(purchase_reference)`，按采购编号查询该单的全部收货，返回与收货结果同结构的列表（每项仅含 `code`、`quantity`、`reference`、`balance`），依提交先后及批内行顺序排列，`balance` 保留登记时的值。采购编号非法或未知抛出 `ValueError`；没有任何收货的单据返回空列表，旧数据缺少收货记录时按空处理。查询不写文件，也不为尚无数据的目录创建文件。
- `return-purchase` → `StockRoom.return_purchase(receipt_reference, quantity, reference)`，按原收货记录办理采购退货。`receipt_reference` 与 `reference` 均为去除首尾空白后的非空字符串（内部空白保留，区分大小写）；`quantity` 为正整数（不接受布尔、小数或字符串），表示退货量。原编号必须指向某张采购单关联的收货记录；同一收货可分多次部分退货，但历次退货累计不得超过该笔收货量，且办理时该物料当前库存不得小于本次退货量。采购单已取消或物料已停用仍允许退货。原编号不属于采购关联收货、数量或编号非法、累计超量、库存不足，以及新编号与任一出入库流水、盘点或冲销编号冲突，均抛出 `ValueError`；校验失败时不写入、不占用编号，`data.json` 的字节与全部业务数据保持原样，尚无文件时不创建，修正后可复用失败请求中的编号。成功时追加一条与退货同编号、数量为退货量相反数的出库流水（库存、`history`、缺料清单随之更新），并在按采购单与原收货关联的退货记录中保存一条记录；返回仅含 `purchase_reference`、`receipt_reference`、`code`、`quantity`（正数退货量）、`reference`、`balance`（退货后的该物料库存）的对象。退货不改变原采购单快照、状态与既有收货记录，不减少既有累计收货量，也不恢复采购收货额度。退货流水不能被 `reverse` 冲销（抛出 `ValueError`）。记录保存在 `root/data.json` 中，重新打开同一目录后仍可查询。
- `return-purchase-batch` → `StockRoom.return_purchase_batch(rows)`，一次提交多笔采购退货，全部校验通过后共同生效。输入对象含非空列表 `rows`，每行只含 `receipt_reference`、`quantity`、`reference` 三个字段，校验口径与 `return-purchase` 完全一致（编号去除首尾空白后非空、内部空白保留且区分大小写，`quantity` 为正整数）。同批可跨采购单和物料，也可多次引用同一收货；已取消采购单和已停用物料仍可退货。按输入顺序逐行计算退货后的库存：历史退货与本批退货合计校验到每笔原收货，累计不得超过该笔收货量；同一物料在不同采购单或收货记录下共用当前库存，任一行扣减后不得为负。新编号不能批内重复，也不能与已有流水、盘点或冲销编号冲突；采购编号仍独立。`rows` 为空或非列表、行非对象或字段缺失多余、字段类型或值非法、原编号不是采购关联收货、编号冲突、累计超退及库存不足，均抛出 `ValueError` 并整批拒绝：不写入、不占用编号，`data.json` 的字节与全部业务数据保持原样，尚无文件时不创建，修正后可复用失败请求中的编号。成功时按输入顺序追加出库流水与退货关联（`history` 与各采购单的退货列表均保留输入顺序），返回与输入等长同序的列表，每项结构与单笔退货结果相同，`balance` 为该行完成后的该物料库存。记录保存在 `root/data.json` 中，重新打开同一目录后仍可查询并继续校验累计限额；旧数据缺少退货记录时按零处理。JSON 数组输入时各批次仍独立提交，先前成功批次保留。
- `purchase-returns` → `StockRoom.purchase_returns(purchase_reference)`，按采购编号查询该单的全部退货，返回与退货结果同结构的列表（每项仅含 `purchase_reference`、`receipt_reference`、`code`、`quantity`、`reference`、`balance`），按登记顺序排列，`balance` 保留登记时的值，不随后续业务改变。采购编号非法或未知抛出 `ValueError`；没有任何退货的单据返回空列表，旧数据缺少退货记录时按空处理。查询不写文件，也不为尚无数据的目录创建文件。
- `purchase-progress` → `StockRoom.purchase_progress(purchase_reference)`，按编号查询单张采购单的收货进度。`purchase_reference` 为去除首尾空白后的非空字符串（内部空白保留，区分大小写）；非字符串、去除首尾空白后为空或编号未知均抛出 `ValueError`。返回采购单原有结构（`reference`、`supplier`、`status`、`rows`，保留单据字段与行顺序），在顶层增加 `progress`，每个 `rows` 元素在采购快照字段（`code`、`name`、`unit`、`quantity`，`quantity` 仍为订购量，名称与单位沿用采购快照）之后增加 `received`、`returned`、`net_received`、`remaining`，均为整数，依次表示累计关联收货量、累计关联退货量、收货减退货的净保留量、订购减累计收货的未收量。所有行均未收货时 `progress` 为 `pending`；全部行累计收货量等于订购量时为 `complete`（即使随后全部退完仍为 `complete`）；其余为 `partial`。退货不增加未收量、不恢复收货额度，也不改变完成进度；普通出入库、盘点和冲销不影响这四项统计。取消单仍可查询，`status` 保留 `cancelled`，进度与未收量照常计算，但仍不能继续收货；物料改名或停用不改变显示的快照与行范围。旧数据缺少收货或退货记录时分别按零计算。查询不改写 `data.json`，成功或失败都不写文件，尚无文件时不创建。
- `purchase-orders` → `StockRoom.purchase_orders(supplier="", status=None, progress=None)`，按条件筛选采购单列表，可省略输入文件（等同默认查询）。`supplier` 默认为空字符串，去除首尾空白后按区分大小写的字面子串匹配单据的供应商名称，空字符串不筛选；`status` 为 `null` 时不筛选，否则只接受 `"open"` 或 `"cancelled"` 并与单据状态原文精确匹配；`progress` 为 `null` 时不筛选，否则只接受 `"pending"`、`"partial"`、`"complete"` 并按进度精确匹配（进度口径与 `purchase-progress` 完全一致：取消状态与收货进度相互独立，退货不恢复未收量，收齐后全部退完仍为 `complete`，普通出入库、盘点、冲销、物料改名和停用不影响统计）。三个条件同时生效，默认包含全部状态和进度。结果按采购编号的 Unicode 码点逐字符升序排列，每项与该编号 `purchase-progress` 的返回对象完全一致（保留采购快照、行顺序与统计口径）。没有采购单或没有匹配时返回空列表；旧数据缺少采购单时按空处理，缺少收货或退货记录时分别按零计算。`supplier` 非字符串，或 `status`、`progress` 不是 `null` 或上述合法值时抛出 `ValueError`，无数据时也照常校验。查询成功或失败都不改写 `data.json` 的字节，不创建缺失的数据文件，不改变库存或业务记录。
- `shortages` → `StockRoom.shortages()`，无参数，可省略输入文件。返回缺料物料对象列表，每项含 `code`、`name`、`unit`、`quantity`（当前台账库存）、`minimum` 与 `shortage`（`minimum` 减 `quantity`）。未设置最低库存的物料按零处理；仅库存严格小于最低库存的物料进入结果，按 `code` 的 Unicode 码点逐字符升序排列。没有缺料或尚未登记物料时返回空列表。查询不改写文件，也不为尚无数据的目录创建文件。
- `replenishment-plan` → `StockRoom.replenishment_plan(keyword="")`，将当前缺料与未收采购量放在同一清单中，可省略输入文件。`keyword` 默认为空字符串，去除首尾空白后按区分大小写的字面子串匹配编码或最新物料名称，空字符串不筛选；`keyword` 不是字符串时抛出 `ValueError`，无数据时也照常校验。查询仅包含启用且库存严格低于最低库存的物料，结果按编码的 Unicode 码点逐字符升序排列，无匹配时返回空列表。每项沿用 `shortages` 的全部字段（名称与单位取最新资料），并增加 `incoming`、`suggested` 与 `purchases`：`incoming` 汇总所有 `open` 采购单中该物料的订购量减累计关联收货量，只纳入未收量大于零的行；`suggested` 为 `shortage` 减 `incoming` 后与零取较大值，建议量为零的缺料物料仍保留；`purchases` 列出参与汇总的来源，每项仅含 `reference`、`supplier`、`remaining`（采购编号、单据供应商和该行未收量），按采购编号的 Unicode 码点升序排列，无来源时为空列表。退货通过出库影响库存，不恢复未收量；取消单和收齐的行不贡献 `incoming`，普通出入库、盘点和冲销只影响库存。对筛选后参与查询的物料，若任一来源行的单位快照与当前单位不一致，整次查询抛出 `ValueError`，不返回部分结果；取消单及未收量为零的行不触发该错误。旧数据缺少物料状态按启用，缺少最低库存按零，缺少采购或收货记录按空处理。查询成功或失败都保持 `root/data.json` 原有字节，不创建缺失的数据文件，也不自动生成采购单。
- `inventory` → `StockRoom.inventory(keyword="", active=None)`，返回当前库存清单对象列表，每项含 `code`、`name`、`unit`、`quantity`、`minimum`、`active`。`keyword` 去除首尾空白后按区分大小写的字面子串匹配编码或名称，空字符串不筛选；`active` 为 `null` 时包含全部物料，为布尔值时只取对应状态；两个条件同时生效，结果按 `code` 的 Unicode 码点逐字符升序排列。
- `export-inventory-csv` → `StockRoom.export_inventory_csv(keyword="", active=None)`，筛选参数与 `inventory` 完全相同，返回承载库存清单的 CSV 字符串。表头固定为 `code,name,unit,quantity,minimum,active`，每行一种物料，名称与单位取最新资料；库存包含出入库、盘点调整与冲销的全部影响；旧数据未记录最低库存或状态时分别输出零和 `true`，停用物料默认保留。整数使用无分组符的十进制文本，状态使用小写 `true` 或 `false`。文本不含 BOM，记录以 LF 结束且末条也有换行；字段包含逗号、双引号、CR 或 LF 时用双引号包裹，内部双引号成对转义，字段内部空白与换行原样保留。无物料或无匹配时仍只返回表头。`keyword` 不是字符串，或 `active` 不是 `null` 或布尔值时抛出 `ValueError`。导出不改变 `data.json` 的字节、库存、配置或任何历史，也不在尚无数据的目录创建文件；数据与筛选条件相同时导出字符串完全相同。命令成功时输出承载 CSV 的 JSON 字符串（JSON 数组输入时输出字符串数组），不扩展 CSV 导入格式。

`code` 与盘点 `reference` 只接受去除首尾空白后的非空字符串，编码区分大小写；`counted` 必须是非负整数（不接受布尔、小数或其他类型），否则抛出 `ValueError`。盘点编号与全部物料的出入库编号共用唯一范围，重复编号抛出 `ValueError`；普通出入库也不能复用零差异盘点占用的编号。校验失败时 `data.json`、库存及两类历史均保持不变。

冲销的 `original_reference` 与 `reference` 同样只接受去除首尾空白后的非空字符串并区分大小写。原编号不存在、指向盘点（含零差异盘点）或冲销记录、或该笔流水已被成功冲销，新编号与任一物料的流水或盘点编号重复，以及冲销后库存为负，均抛出 `ValueError`；查询未知物料的冲销列表也抛出 `ValueError`。每笔普通流水最多成功冲销一次。拒绝操作后 `data.json`、库存和全部历史保持不变，不新增编号占用。冲销关联保存在 `root/data.json` 中，重新打开同一目录仍可查询。

最低库存的 `code` 同样去除首尾空白后匹配并区分大小写；编码不是非空字符串、物料不存在或 `minimum` 不是非负整数时抛出 `ValueError`，此时 `data.json` 与全部业务查询结果保持不变，不占用流水编号。最低库存保存在 `root/data.json` 中，重新打开同一目录仍生效；旧数据目录无需补写配置即可直接查询缺料清单，后续出入库、盘点和冲销按最新库存影响清单。

停用状态的 `code` 同样去除首尾空白后匹配并区分大小写；编码不是非空字符串、物料不存在或 `active` 不是布尔值时抛出 `ValueError`，此时 `data.json` 的字节、库存、状态、最低库存配置及全部历史保持原样，不占用流水编号。停用物料的普通出入库无论数量正负均抛出 `ValueError`；批量出入库遇到停用物料时整批拒绝，其他合法行也不保存。状态保存在 `root/data.json` 中，重新打开同一目录仍生效，状态查询不写文件。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前仅支持一个仓库及整数数量（盘点实点数量同样为非负整数）。没有库位和多进程并发控制。不承诺并发写入或断电恢复。
