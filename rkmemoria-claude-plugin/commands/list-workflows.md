# /rai:list-workflows

List workflows available on the platform.

## Steps

1. Call `rkm_list_workflows`.
2. Display as a table:
   ```
   ID   Slug                          Name                            Steps
   1    extract-customer-insights     Extract Customer Insights       3
   2    onboarding-summary            Onboarding Summary              2
   ```
3. If no workflows, suggest creating one in the platform UI.
