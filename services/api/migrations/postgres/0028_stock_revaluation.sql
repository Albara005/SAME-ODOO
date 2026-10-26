-- 0028: a vendor billing another price than the order's revalues the goods still in stock:
-- stock moves of kind 'revaluation' change value only, so their quantity is zero.
ALTER TABLE stock_moves DROP CONSTRAINT ck_stock_move_nonzero;
ALTER TABLE stock_moves ADD CONSTRAINT ck_stock_move_nonzero CHECK (CAST(quantity AS NUMERIC) <> 0 OR kind = 'revaluation');
