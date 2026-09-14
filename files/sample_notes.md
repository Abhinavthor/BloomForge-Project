# DBMS — Quick Notes

Normalization organizes tables to reduce data redundancy and avoid update, insert,
and delete anomalies. 1NF removes repeating groups; 2NF removes partial dependencies;
3NF removes transitive dependencies.

A primary key uniquely identifies each row and cannot be NULL. A foreign key
references the primary key of another table to enforce referential integrity.

Indexes speed up lookups. Most databases implement indexes as B-trees, which keep
keys sorted so range queries and point lookups are fast.

Joins combine rows from tables. INNER JOIN keeps only matching rows; LEFT JOIN keeps
all rows from the left table even if there is no match on the right.

Transactions follow ACID: Atomicity (all-or-nothing), Consistency (valid states only),
Isolation (concurrent transactions don't interfere), Durability (committed data survives
crashes).

Aggregation with GROUP BY buckets rows so functions like SUM, COUNT, and AVG run per
group. COUNT(column) ignores NULLs, while COUNT(*) counts every row.
