[CmdletBinding()]
param()

# Current user rule: warehouse item 1038725569412 / SKU 6060112621275 is
# excluded from signup and price changes. This compatibility wrapper must not
# contact NAS, ERP API, Web-Agent, or Taobao.
([ordered]@{
    ok = $false
    error = 'user_rule_excluded'
    reason = 'warehouse_item_no_signup_no_price_change'
    item_id = '1038725569412'
    sku_id = '6060112621275'
    claim_created = $false
    web_agent_called = $false
    platform_write = $false
    price_change = $false
}) | ConvertTo-Json -Compress
