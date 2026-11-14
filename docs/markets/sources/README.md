# Provider source documents

One folder per provider, named after its id in `../providers.csv`
(`fedex/`, `ups/`, `dhl_express/`, ...). Put downloaded rate guides,
zone charts, surcharge sheets and quote emails here.

The documents themselves are not committed (decided 07-Nov-2026: carrier
rate files are often for registered customers only, e.g. FedEx Tariff
Pricing, so they must not be redistributed through the repo). Only each
folder's `README.md` is committed: it lists the files expected there,
where to get them, and which CSV rows were read from them.

When a file is added, the rows taken from it point to it in their
`source` column (e.g. `sources/fedex/fedex-rates-g3pie-en-vn-2026.pdf`)
and keep the public `source_url`.
