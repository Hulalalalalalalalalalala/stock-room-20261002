import argparse
import json
from pathlib import Path
import sys
import tempfile
from . import StockRoom

ACTIONS = {'register': 'register', 'import-materials-csv': 'import_materials_csv', 'update-material': 'update_material', 'move': 'movement', 'move-batch': 'movement_batch', 'stock': 'stock', 'history': 'history', 'movement-ledger': 'movement_ledger', 'count': 'count', 'count-batch': 'count_batch', 'counts': 'counts', 'reverse': 'reverse', 'reversals': 'reversals', 'set-minimum': 'set_minimum', 'shortages': 'shortages', 'replenishment-plan': 'replenishment_plan', 'inventory': 'inventory', 'export-inventory-csv': 'export_inventory_csv', 'set-active': 'set_active', 'material-status': 'material_status', 'create-purchase': 'create_purchase', 'purchase-order': 'purchase_order', 'cancel-purchase': 'cancel_purchase', 'update-purchase': 'update_purchase', 'receive-purchase': 'receive_purchase', 'purchase-receipts': 'purchase_receipts', 'return-purchase': 'return_purchase', 'return-purchase-batch': 'return_purchase_batch', 'preview-purchase-returns': 'preview_purchase_returns', 'purchase-returns': 'purchase_returns', 'purchase-progress': 'purchase_progress', 'purchase-orders': 'purchase_orders', 'save-supplier': 'save_supplier', 'suppliers': 'suppliers', 'supplier-record': 'supplier_record'}

def samples(name):
    return json.loads((Path(__file__).resolve().parent.parent / "examples" / name).read_text(encoding="utf-8"))

def demo(app):
    for material in samples("materials.json"):
        app.register(**material)
    for row in samples("movements.json"):
        app.movement(**row)
    return app.stock("PAPER")

def main(argv=None):
    parser = argparse.ArgumentParser(description="仓库物料台账")
    parser.add_argument("--root", required=True, help="local data directory")
    parser.add_argument("action", choices=[*ACTIONS, "demo"])
    parser.add_argument("input", nargs="?", help="UTF-8 JSON object, or array of objects, containing API arguments")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            # Sample operations run in a fresh child directory and never overwrite user's data.
            Path(args.root).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sample-", dir=args.root) as location:
                value = demo(StockRoom(location))
        else:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else {}
            app = StockRoom(args.root)
            method = getattr(app, ACTIONS[args.action])
            if isinstance(payload, list):
                value = []
                for row in payload:
                    if not isinstance(row, dict):
                        raise ValueError("each input must be an object")
                    value.append(method(**row))
            elif isinstance(payload, dict):
                value = method(**payload)
            else:
                raise ValueError("input must be an object or array")
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
