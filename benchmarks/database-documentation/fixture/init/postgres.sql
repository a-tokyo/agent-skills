-- Synthetic public fixture (PostgreSQL) for the database-documentation benchmark.
-- Deliberately exercises ONE OF EVERY object class the parity scorer checks, so a clean docker-compose-up
-- gives an outsider a fully reproducible target. Hand-authored; the held-out TEST set.

-- extension (its functions must NOT appear in the docs -> tests extension-exclusion)
CREATE EXTENSION IF NOT EXISTS citext;

-- native enum type
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'cancelled');

-- explicit sequence (beyond serial/identity)
CREATE SEQUENCE invoice_number_seq START 1000;

-- self-referencing hierarchy + table comment
CREATE TABLE category (
  id          int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        varchar(120) NOT NULL,
  parent_id   int REFERENCES category(id) ON DELETE SET NULL,
  slug        citext NOT NULL UNIQUE                       -- case-insensitive unique
);
COMMENT ON TABLE category IS 'Product categories, self-nesting via parent_id.';
COMMENT ON COLUMN category.slug IS 'URL slug (case-insensitive).';

CREATE TABLE customer (
  id          int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email       citext NOT NULL,
  full_name   varchar(200) NOT NULL,
  kind        varchar(16) NOT NULL DEFAULT 'individual'
                CHECK (kind IN ('individual', 'business')),  -- string+CHECK "enum"
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz                                    -- soft delete
);
-- expression + partial unique index (uniqueness only among non-deleted rows, case-insensitive)
CREATE UNIQUE INDEX uq_customer_email_active ON customer (lower(email)) WHERE deleted_at IS NULL;

CREATE TABLE product (
  id           int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  category_id  int NOT NULL REFERENCES category(id) ON DELETE RESTRICT,
  sku          varchar(40) NOT NULL,
  name         varchar(200) NOT NULL,
  price_cents  int NOT NULL CHECK (price_cents >= 0),
  price_usd    numeric(10,2) GENERATED ALWAYS AS (price_cents / 100.0) STORED,  -- generated column
  attributes   jsonb NOT NULL DEFAULT '{}'::jsonb,                              -- JSON column
  CONSTRAINT uq_product_sku UNIQUE (sku)
);
CREATE INDEX idx_product_category ON product (category_id);
CREATE INDEX idx_product_attrs_gin ON product USING gin (attributes);            -- non-btree method

CREATE TABLE "order" (
  id           int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  customer_id  int REFERENCES customer(id) ON DELETE SET NULL,
  status       order_status NOT NULL DEFAULT 'pending',
  invoice_no   int NOT NULL DEFAULT nextval('invoice_number_seq'),
  placed_at    timestamptz NOT NULL DEFAULT now(),
  total_cents  int NOT NULL DEFAULT 0
);
CREATE INDEX idx_order_customer ON "order" (customer_id);

-- composite primary key + two FKs (different ON DELETE) + composite unique
CREATE TABLE order_item (
  order_id    int NOT NULL REFERENCES "order"(id) ON DELETE CASCADE,
  product_id  int NOT NULL REFERENCES product(id) ON DELETE RESTRICT,
  quantity    int NOT NULL DEFAULT 1 CHECK (quantity > 0),
  unit_cents  int NOT NULL,
  PRIMARY KEY (order_id, product_id)
);

-- view
CREATE VIEW active_customers AS
  SELECT id, email, full_name FROM customer WHERE deleted_at IS NULL;

-- routine (function) + trigger that keeps updated_at fresh
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;
CREATE TRIGGER trg_customer_updated_at
  BEFORE UPDATE ON customer
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- a little seed data so row-count / sampling paths have something to read
INSERT INTO category (name, slug) VALUES ('Books', 'books'), ('Electronics', 'electronics');
INSERT INTO customer (email, full_name) VALUES ('a@example.com', 'Ada L'), ('g@example.com', 'Grace H');
INSERT INTO product (category_id, sku, name, price_cents) VALUES (1, 'BK-1', 'SQL Basics', 2999);
