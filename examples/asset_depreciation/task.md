Every month I update our fixed-asset depreciation schedule in Excel by hand and it is easy
to make mistakes. Attached is the asset register (asset_register.xlsx).

Please produce `depreciation_2026.xlsx` with
- sheet "Schedule": one row per asset and month of 2026 while the asset is on our books:
  Asset ID, Month (2026-01 ... 2026-12), Opening NBV, Depreciation, Closing NBV
- sheet "Category Totals": Category, Assets (how many assets appear in the schedule),
  Depreciation 2026 (total for the year)

Our policy:
- Straight-line: (Cost - Salvage Value) / Useful Life (months), rounded to cents.
- Depreciation starts in the month AFTER the acquisition month. In the acquisition month
  the asset is on the books but has zero depreciation.
- The last month takes the rounding difference so the asset ends exactly at its salvage value,
  and it never goes below salvage value.
- If an asset was disposed, it is depreciated up to and including the disposal month and does
  not appear after that.
- Assets that are already fully depreciated still appear with zero depreciation.
