-- Synthetic public fixture (SQL Server) for the database-docs benchmark.
-- Same logical schema as postgres.sql, rendered in T-SQL to exercise SQL Server's object classes:
-- IDENTITY, computed columns, filtered indexes, CHECK-as-enum, datetimeoffset, sequence, trigger, function,
-- view, composite PK, FKs with varied delete actions. (SQL Server has no native ENUM type or citext.)

-- filtered indexes (and other features) require these SET options; set them explicitly so seeding works
-- regardless of which sqlcmd build applies the script (the container's mssql-tools defaults QUOTED_IDENTIFIER OFF).
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

CREATE SEQUENCE invoice_number_seq AS int START WITH 1000;
GO

CREATE TABLE category (
  id         int IDENTITY(1,1) CONSTRAINT pk_category PRIMARY KEY,
  name       nvarchar(120) NOT NULL,
  parent_id  int NULL CONSTRAINT fk_category_parent REFERENCES category(id),  -- self-ref (NO ACTION; MSSQL forbids cascade cycles)
  slug       nvarchar(160) NOT NULL CONSTRAINT uq_category_slug UNIQUE
);
GO

CREATE TABLE customer (
  id          int IDENTITY(1,1) CONSTRAINT pk_customer PRIMARY KEY,
  email       nvarchar(320) NOT NULL,
  full_name   nvarchar(200) NOT NULL,
  kind        nvarchar(16) NOT NULL CONSTRAINT df_customer_kind DEFAULT 'individual'
                CONSTRAINT ck_customer_kind CHECK (kind IN ('individual','business')),  -- string+CHECK "enum"
  created_at  datetimeoffset NOT NULL CONSTRAINT df_customer_created DEFAULT SYSDATETIMEOFFSET(),
  updated_at  datetimeoffset NOT NULL CONSTRAINT df_customer_updated DEFAULT SYSDATETIMEOFFSET(),
  deleted_at  datetimeoffset NULL                                            -- soft delete
);
GO
-- filtered unique index (uniqueness only among non-deleted rows)
CREATE UNIQUE INDEX uq_customer_email_active ON customer (email) WHERE deleted_at IS NULL;
GO

CREATE TABLE product (
  id           int IDENTITY(1,1) CONSTRAINT pk_product PRIMARY KEY,
  category_id  int NOT NULL CONSTRAINT fk_product_category REFERENCES category(id),  -- NO ACTION
  sku          nvarchar(40) NOT NULL CONSTRAINT uq_product_sku UNIQUE,
  name         nvarchar(200) NOT NULL,
  price_cents  int NOT NULL CONSTRAINT ck_product_price CHECK (price_cents >= 0),
  price_usd    AS (CAST(price_cents AS decimal(10,2)) / 100.0),               -- computed column
  attributes   nvarchar(max) NOT NULL CONSTRAINT df_product_attrs DEFAULT '{}' -- JSON-in-nvarchar(max)
);
GO
CREATE INDEX idx_product_category ON product (category_id);
GO

CREATE TABLE orders (
  id           int IDENTITY(1,1) CONSTRAINT pk_orders PRIMARY KEY,
  customer_id  int NULL CONSTRAINT fk_orders_customer REFERENCES customer(id) ON DELETE SET NULL,
  status       nvarchar(16) NOT NULL CONSTRAINT df_orders_status DEFAULT 'pending'
                 CONSTRAINT ck_orders_status CHECK (status IN ('pending','paid','shipped','cancelled')),
  invoice_no   int NULL,   -- populated app-side via NEXT VALUE FOR invoice_number_seq (sequence kept as a standalone object)
  placed_at    datetimeoffset NOT NULL CONSTRAINT df_orders_placed DEFAULT SYSDATETIMEOFFSET(),
  total_cents  int NOT NULL CONSTRAINT df_orders_total DEFAULT 0
);
GO
CREATE INDEX idx_orders_customer ON orders (customer_id);
GO

CREATE TABLE order_item (
  order_id    int NOT NULL CONSTRAINT fk_oi_order REFERENCES orders(id) ON DELETE CASCADE,
  product_id  int NOT NULL CONSTRAINT fk_oi_product REFERENCES product(id),  -- NO ACTION
  quantity    int NOT NULL CONSTRAINT df_oi_qty DEFAULT 1 CONSTRAINT ck_oi_qty CHECK (quantity > 0),
  unit_cents  int NOT NULL,
  CONSTRAINT pk_order_item PRIMARY KEY (order_id, product_id)   -- composite PK
);
GO

CREATE VIEW active_customers AS
  SELECT id, email, full_name FROM customer WHERE deleted_at IS NULL;
GO

-- scalar function (routine)
CREATE FUNCTION dbo.fn_dollars(@cents int) RETURNS decimal(10,2)
AS BEGIN RETURN CAST(@cents AS decimal(10,2)) / 100.0 END;
GO

-- trigger that keeps updated_at fresh
CREATE TRIGGER trg_customer_updated_at ON customer AFTER UPDATE AS
BEGIN
  SET NOCOUNT ON;
  UPDATE c SET updated_at = SYSDATETIMEOFFSET()
  FROM customer c JOIN inserted i ON c.id = i.id;
END;
GO

INSERT INTO category (name, slug) VALUES ('Books','books'), ('Electronics','electronics');
INSERT INTO customer (email, full_name) VALUES ('a@example.com','Ada L'), ('g@example.com','Grace H');
INSERT INTO product (category_id, sku, name, price_cents) VALUES (1,'BK-1','SQL Basics',2999);
GO
