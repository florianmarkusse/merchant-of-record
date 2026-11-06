# 07 — Keycaps, ceramics, lacquerware, wooden goods (scope extension)

Researched 2026-11-06, a short pass (4 searches) after the candidate
merchant list widened the scope beyond apparel: artisan resin keycaps and
desk tech (Artkey Universe, KiwiKey, Jelly Key), handmade ceramic
tableware (Gốm Chi), and lacquerware (Hanoia). Labels as in `05`: [P]
primary, [A] adviser or lab, [N] news or vendor. Most of this is
**UNVERIFIED** and needs a test lab or a customs broker.

## Summary

| Category | Main rule | Markets | What we need per SKU | Gate at launch |
|---|---|---|---|---|
| Ceramic **tableware** (cups, plates, bowls: anything that touches food) | Lead and cadmium leaching limits. EU Directive 84/500/EEC (e.g. hollowware lead 4.0 mg/L, cadmium 0.3 mg/L) plus a declaration of compliance [A]. NL set stricter national limits on 2026-05-29 (old stock until 2026-12-01) [A: SGS, Intertek, RTS]. EU revision still under discussion, not adopted [A]. US FDA action levels per article type (CPG 545.450) [A, figures conflict]. California Prop 65 warning [N, figures unreliable] | EU, NL, US, CA (California); others likely | `food_contact: bool`; leaching test report (lab, standard e.g. ISO 6486, date) per glaze and article type; EU declaration of compliance | Allow with a test report; block food-contact SKUs to the EU and US without one. Decorative-only pieces must say "not for food use" |
| **Lacquerware** (sơn mài: plant lacquer over wood or bamboo) | Wood and plant material: US **Lacey Act** declaration (APHIS PPQ 505, species and origin) for HS codes on the APHIS schedule [A]. **EU Deforestation Regulation** applies from **2026-12-30** to large and medium operators, with 6 more months for micro and small ones. Whether wood already under the EU Timber Regulation gets the extra time is disputed between sources [A: EY, Council]. Food contact: no rule found for lacquer surfaces UNVERIFIED. Urushiol in the lacquer sap is a skin allergen when not fully cured [N] | US, EU (+ AU illegal-logging rules, not checked) | wood species (scientific name) and harvest country; `food_contact`; EUDR due-diligence data (geolocation of harvest) if HS 44 or 94 is in scope | Allow decorative pieces. Food-contact lacquer: block until a lab confirms. EU: decide before 2026-12-30 who is the EUDR operator (likely the importer: us, if we are importer of record) |
| **Wooden** goods generally (boxes, trays, bamboo) | Same Lacey Act and EUDR points as lacquerware; fumigation and ISPM 15 apply to wood packaging, not to finished goods | US, EU, AU | species, origin | as lacquerware |
| **Artisan keycaps** (resin, PBT, ABS) | Customs: heading 8473.30 (parts of keyboards) is duty-free in the US general column. Some classifiers put plastic keycaps under 3926 instead, at a higher rate; no binding ruling found [N: forum, classifier sites]. ITA 0% status for keycaps not confirmed. Product rules: none specific found; general product safety (EU GPSR, US CPSA) and chemical limits (REACH, Prop 65 for resin additives such as BPA, UNVERIFIED). Collectible keycaps are not children's products, but small parts make the age grade matter in the US | All | HS code with source; `age_grade = adult` (collector item); resin type | Allow. Declare carefully: a 3926 vs 8473.30 choice changes duty, and the duty quote must use the same code as the declaration |
| **Keyboards and desk tech** (KiwiKey, Jelly Key may sell complete boards) | Electronics: EU CE (EMC, RoHS; RED if wireless), UK UKCA, US FCC; WEEE take-back registration per EU country; **lithium batteries** in wireless boards are dangerous goods for air (UN3481), restricted per courier | EU, UK, US, and most markets | battery yes/no and type; radio yes/no; conformity documents | **Exclude** boards with batteries or radios at launch (DG and radio approvals). Wired boards: allow with a CE/RoHS declaration |

## Notes for the design

- `product_category` needs the values `ceramic_tableware`,
  `ceramic_decorative`, `lacquerware`, `wooden_goods`, `keycaps`,
  `keyboard_wired`, `keyboard_wireless`, beside the apparel and
  accessory categories in `research-product-compliance.md` §3.
- Two new SKU fields: `food_contact` and `wood_species` (scientific name
  plus harvest country).
- Customs classification matters more for keycaps and ceramics than for
  apparel, because the plausible HS codes have different rates. The HS
  code used for the duty quote and the one on the courier declaration must
  be the same stored value.
- The EUDR date (2026-12-30) is close: if Hanoia-type merchants sell
  wooden or lacquered goods into the EU, we decide who is the operator
  before opening the EU for those categories.

## Questions for counsel or a lab

1. Ceramics: which limits apply per market for handmade tableware, and is
   one test standard accepted across the EU, US and AU?
2. Lacquerware: is cured plant lacquer acceptable for food contact in the
   EU and US?
3. EUDR: who is the operator when a non-EU seller ships DDP to an EU
   consumer, and do HS 44 handicrafts get the micro and small operator
   delay?
4. Keycaps: a binding classification (US CBP ruling, EU BTI), so that
   every merchant declares the same code.

## Sources

- https://www.sgs.com/en/news/2026/05/safeguards-07626-the-netherlands-revises-rules-for-food-contact-ceramics
- https://www.intertek.com/products-retail/insight-bulletins/2026/1542-netherlands-tightens-safety-requirements-food-contact-ceramics/
- https://www.irtslab.com/news/netherlands-tightens-lead-cadmium-limits-for-food-contact-ceramics-effective-may-29-2026/
- https://www.britglass.org.uk/knowledge-base/digital-library-and-information-services/ec-considering-revision-council-directive
- https://www.conphora.com/regulations/cer/
- https://fennhome.com/resource/ceramic-tableware-compliance-guide-eu-us/ (vendor)
- https://taxnews.ey.com/news/2026-0237-eu-deforestation-regulation-application-postponed-to-30-december-2026
- https://www.consilium.europa.eu/en/press/press-releases/2025/12/18/deforestation-council-signs-off-targeted-revision-to-simplify-and-postpone-the-regulation/
- https://www.compliancegate.com/united-states-wood-and-bamboo-product-regulations/
- https://www.afslaw.com/perspectives/alerts/additional-wood-products-require-lacey-act-import-declaration-phase-six
- https://www.keebtalk.com/t/keyboard-parts-are-hit-by-the-new-trump-china-tariffs/3626 (forum)
- https://www.htshub.com/us-hs/search/8473
