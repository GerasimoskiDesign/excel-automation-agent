At the end of each month I go through the time-clock export line by line to find problems
for HR. Attached: the September export (clock_events_2026-09.csv) and our roster with
everyone's shift start (roster.xlsx).

I need `attendance_exceptions.xlsx` with three sheets:
- "Missing Clock-In": Employee ID, Name, Date - days where there is a clock-out but no clock-in
  (the time clock writes "-" when a punch is missing)
- "Repeated Late": Employee ID, Name, Late Days, Late Dates - everyone who was late 3 or more
  times in the month. Late means clocking in more than 5 minutes after their shift start.
  Late Dates is a comma-separated list like "2026-09-03, 2026-09-10".
- "Out Before In": Employee ID, Name, Date, Clock In, Clock Out - rows where the clock-out
  time is earlier than the clock-in time (somebody typed the times the wrong way round).
  These rows are data errors, so please do not count them as late.
Everything sorted by Employee ID and date. Days without any row are absences, HR has those
already, so ignore them.
