# Offboarding report: temp.tester@test-brand.example

Ran 2026-10-18T12:28:48+00:00 in **simulation** mode. 0 completed, 4 pending, 0 blocked.

| Action | Status | Detail |
|---|---|---|
| remove insights_viewer on northwind-test/business_suite | pending | task 16 with ops.owner@test-brand.example |
| rotate password for @northwind.test.co | pending | new password staged in vault; task 17 |
| replace backup codes for @northwind.test.co | pending | old set marked exposed; task 18 |
| revoke vault access | pending | waits until every exposed secret is rotated |
