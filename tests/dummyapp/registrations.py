from core.approvals import StepSpec, register_chain, register_target
from core.crud.registry import Inline, register_resource
from core.reports.registry import ReportColumn, register_report_source

from .models import Buyer, Fabric, Garment, GarmentLine, LineOption, Order

# row-level access: users see only the buyers assigned to them, orders follow their buyer, and each user sees their own orders
# plus those of the users assigned to them (see core.permissions.access)
from core.permissions.access import register_access_scope  # noqa: E402

register_resource(Buyer, slug="buyers", list_fields=("name",), form_fields=("name",), searchable_fields=("name",))
register_resource(Order, slug="orders", list_fields=("ref", "buyer", "qty", "created_by"), form_fields=("ref", "buyer", "qty"),
                  searchable_fields=("ref",), filterable_fields=("buyer",), owner_field="created_by")
register_access_scope(Buyer)
register_access_scope(Buyer, target=Order, path="buyer")
register_resource(Fabric, slug="fabrics", list_fields=("name",), form_fields=("name",), searchable_fields=("name",))
register_resource(
    Garment, slug="garments", list_fields=("name", "style_no", "price", "status", "fabric"),
    form_fields=("name", "style_no", "price", "status", "ordered_on", "is_active", "fabric"),
    searchable_fields=("name", "style_no"), filterable_fields=("name", "style_no", "price", "status", "ordered_on", "is_active", "fabric"),
    inlines=(Inline(GarmentLine, fields=("description", "qty", "position"), slug="lines", label="Line", position_field="position", hard_delete=True,
                    nested=(Inline(LineOption, fields=("label", "position"), slug="options", label="Option", position_field="position"),)),),
    allow_import=True)
register_report_source(
    "garments", Garment, label="Garments",
    columns=[ReportColumn("name", "Name", "name", "text", groupable=True, aggregates=("count",)),
             ReportColumn("status", "Status", "status", "choice", groupable=True, aggregates=("count",)),
             ReportColumn("price", "Price", "price", "number", aggregates=("sum", "avg", "min", "max", "count")),
             ReportColumn("style_no", "Style no.", "style_no", "text")],
    searchable_fields=("name", "style_no"), filterable_fields=("name", "status", "price"), default_columns=("name", "status", "price"))
register_chain("garment", "Garment approval",
               [StepSpec("merch", "Merchandiser", "dummyapp.change_garment"), StepSpec("manager", "Manager", "dummyapp.approve_garment")])
register_target("garment", Garment)


# ---- custom report (code-defined): a one-garment "work order" document -- the shape of docs/REPORT_BUILDER.md, section 6
from django import forms  # noqa: E402

from core.approvals.signatures import approval_signatures  # noqa: E402
from core.reports.custom import ReportOutput, register_custom_report  # noqa: E402


class GarmentSheetParams(forms.Form):
    style_no = forms.CharField(max_length=30, label="Style no.")


def garment_sheet(ctx, params):
    garment = Garment.objects.filter(company_id__in=ctx.company_ids, style_no=params["style_no"]).first()   # scope: active company
    if garment is None:
        return ReportOutput(columns=[("description", "Description"), ("qty", "Qty")], rows=[], subtitle="No garment with that style no.")
    rows = [{"no": n, "description": line.description, "qty": line.qty} for n, line in enumerate(garment.lines.order_by("position", "pk"), 1)]
    return ReportOutput(
        title=f"Garment Sheet - {garment.style_no}", notice="Quote the style no. on every delivery challan",
        info=[("Garment", garment.name), ("Status", garment.get_status_display()), ("Price", garment.price)],
        columns=[("no", "#"), ("description", "Description"), ("qty", "Qty")], rows=rows, numeric=("qty",),
        totals={"qty": sum(r["qty"] for r in rows)}, notes=["Remarks & terms: goods are checked on arrival."],
        signatures=approval_signatures(garment, labels={"merch": "Checked By", "manager": "Approved By"}))


register_custom_report("garment-sheet", "Garment sheet", garment_sheet, form=GarmentSheetParams, permission="dummyapp.view_garment",
                       description="One garment with its lines, totals and approval signatures.", category="Production")


# ---- query builder: garment lines have no list page of their own, so they are allowed explicitly (docs/QUERY_BUILDER.md)
from core.reports.query import register_query_model  # noqa: E402

register_query_model(GarmentLine, label="Garment lines")


# ---- stock ledger (opening balance + receipts + issues + running balance) over a movement model, shown in the menu under
# "Inventory > Fabric" once that group exists (docs/REPORT_BUILDER.md, section 6.7)
from core.reports.custom import register_stock_ledger  # noqa: E402

from .models import StockMove  # noqa: E402

register_stock_ledger("fabric-ledger", "Fabric stock ledger", StockMove, item_field="fabric", date_field="moved_on", qty_in="qty_in",
                      qty_out="qty_out", columns=(("ref", "Challan no."),), category="Inventory", menu="Inventory > Fabric",
                      menu_label="Stock ledger", menu_position=40)


# ---- background job (docs/JOBS.md): a handler run by the worker; the run, its progress and its CSV appear under Background jobs
from core.jobs import register_job  # noqa: E402


@register_job("dummyapp.garment_price_list", label="Garment price list", timeout=600)
def garment_price_list(job):
    rows = Garment.objects.filter(company_id=job.company_id) if job.company_id else Garment.objects.all()
    rows = list(rows.order_by("style_no").values_list("style_no", "name", "price"))
    job.set_progress(50, f"{len(rows)} garments read")
    if job.params.get("fail"):
        raise ValueError("asked to fail (password=hunter2)")
    job.save_file("garment-prices.csv", "\n".join(["style_no,name,price", *(f"{s},{n},{p}" for s, n, p in rows)]).encode())
    return {"garments": len(rows)}
