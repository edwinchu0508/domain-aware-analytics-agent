# Database assumptions

Supply your own PostgreSQL Olist import. No data, dump or database credentials are distributed. The evaluated server was PostgreSQL 18.6, with these public tables:

- `customers`: order-associated customer records; `customer_unique_id` represents cross-order customer identity.
- `orders`: order-level purchase/status and delivery timestamps.
- `order_items`: item-level merchandise amounts, freight and product references; multiple rows per order are expected.
- `payments`: payment-record grain; multiple records per order are possible.
- `reviews`: review records; neither row count nor an order join automatically establishes the intended review counting unit.
- `products`: product attributes, joined through the item product reference.

The historical table counts were customers 99,441; orders 99,441; order_items 112,650; payments 103,886; reviews 99,224; products 32,951. These counts describe the study, not required counts for your installation or proof of content identity.

The adapter discovers actual columns from `information_schema` and trusted non-deferrable validated PK/UNIQUE constraints from PostgreSQL catalogs. It does not assume that this Olist import declares such constraints, infer uniqueness from equal column names, or create missing constraints. Missing constraint evidence can limit semantic proof.

The dedicated role must have SELECT on the required tables, no INSERT/UPDATE/DELETE/TRUNCATE, no CREATE in public, and neither superuser nor BYPASSRLS. Use real base tables, not arbitrary views or user-defined functions, with a trusted search path/database configuration. Also configure the role's default read-only setting and five-second timeout through an administrator before use; this repository deliberately does not provision roles or mutate a database.

Each metadata connection is explicitly read-only with five-second timeout. Primary and diagnostic SQL within an agent session share one read-only repeatable-read snapshot. A result cursor enforces complete-result budgets. Always use a new, non-sensitive dataset for demonstrations and keep generated local traces private.

The domain dictionary contains project-curated Olist conventions, not an independent business-standard authority. For example, merchandise excludes freight, but the intended population for an average must still be stated by the user. Ambiguity must be disclosed or clarified.
