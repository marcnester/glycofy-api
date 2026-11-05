# Ingredient purchasing rules

Recipe ingredient quantities and nutrition remain unchanged. Grocery aggregation maps explicitly compatible ingredient names to a shared purchasing identity. Plain raw, fresh, and frozen cod fillets share one shopping row. Generic lettuce uses romaine as its shopping choice; explicitly named iceberg and butter lettuce remain separate. Cooked and breaded fish remain separate.

QFC product search and cart review compare approved shopping amounts with fixed retailer package sizes. Package counts round up to cover the required quantity. Users can change counts and see shortages. A package size does not establish the number or size of individual fish fillets. Count-only produce cannot be converted to weight without a declared weight. Ambiguous and variable-weight packages receive no inferred coverage.

Coverage checks quantity only; shoppers still review product suitability and availability. The server recalculates coverage during cart review. Existing explicit cart confirmation, store handoff, authorization and duplicate-write protections remain required. No order is placed by this flow.

Grocery schema 4 requires fresh review and approval after identity changes, and isolates local grocery overrides from the previous schema. No meal generation, portion, nutrition, or database schema change is included.
