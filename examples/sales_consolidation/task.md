Hi! Every month I get one Excel file per shop (North, Harbour, Station) with all the
sales lines, and I copy-paste them into one summary by hand. It takes me most of a morning.

What I need: one workbook `sales_summary.xlsx` with
- a sheet "Summary": one row per shop and month with Shop, Month (like 2026-07),
  Invoices (how many invoices), Units and Revenue
- a sheet "Shop Totals": one row per shop with Invoices, Units, Revenue, and a last row
  "All shops" with the grand total.

Things I know about the files:
- The shop is in the file name, e.g. sales_harbour_2026-08.xlsx.
- Harbour adds a TOTAL row at the bottom of every file - that must not be counted twice.
- Station's till exports different column names and the dates as text (day.month.year).
- North's files sometimes have empty rows in the middle.
- Refunds have an R in the invoice number and a negative quantity. They are not invoices,
  but they must reduce units and revenue.
I attached three months of files and last quarter's summary as I would do it by hand.
