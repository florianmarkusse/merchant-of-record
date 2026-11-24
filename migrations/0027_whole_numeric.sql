-- quote_part's exact fraction is numeric only because it can outgrow bigint
-- (0022); it is always a whole numerator and denominator. Code reads
-- numeric as int (src/mor/db/numeric.py), so the database keeps the scale
-- at 0: a fractional value is refused here, not found on reading.
ALTER TABLE quote_part
  ADD CONSTRAINT quote_part_exact_whole
  CHECK (scale(exact_numerator) = 0 AND scale(exact_denominator) = 0);
