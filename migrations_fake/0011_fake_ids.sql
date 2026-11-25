-- The fakes' ids come from this sequence, never at random: ids then sort
-- in the order they were made, so a fuzzer's replay meets payments, events
-- and batches in the same order (fuzzing conventions: every variation comes
-- from a draw). Not restarted between examples; only the order matters.
CREATE SEQUENCE fake_id;
