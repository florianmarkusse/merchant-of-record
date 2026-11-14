# 05 — Materials, IP, carriers, forced labour, rest-of-world (cross-cutting)

Researched 06-Nov-2026. The shared web-search budget ran out after about 20 searches,
before the IP, GCC, LatAm and ZA questions could be checked. Those sections come
from background knowledge and are marked **UNVERIFIED**. Labels: [P] primary or
official, [A] adviser or law firm, [N] news or vendor.

Who owes each duty: as merchant of record (MoR) we are the seller of record. Selling
DDP, we are in practice also the importer of record (IOR) or the person the courier
clears for. So most of these duties are **ours**, and we pass them back to the
merchant by contract (warranty, test reports, indemnity). The merchant's role is to
supply the evidence.

## Summary table

| # | Topic | Rule | Markets | Who owes | Catalogue screen / data per SKU | Confidence |
|---|---|---|---|---|---|---|
| 1 | Exotic leathers (python, crocodile, some stingray, coral, giant clam) | CITES App. II/I: VN export permit + destination import permit (EU Annex B, others); personal-effects exemption does **not** cover goods bought remotely and shipped | All | Us (exporter/IOR) + merchant (permit applicant in VN) | `species_scientific_name`, `cites_appendix`, `source_code` (C/D/W), VN CITES export permit no. per shipment | High on principle [P/N]; VN permit mechanics partly verified |
| 2 | Any wildlife product into US (incl. non-CITES exotic leather, shells, feathers) | USFWS Form 3-177, designated port, commercial wildlife import/export licence (50 CFR 14.91) | US | IOR (us) | Flag `contains_wildlife` | Medium [P forms]; licence detail UNVERIFIED |
| 3 | Ivory, elephant/rhino/sea-turtle/whale products, tortoiseshell | Near-total sale/import bans (US ESA 4(d), UK Ivory Act 2018, EU 2022, HK, CN) | All | Us | **Hard block** | High (background) |
| 4 | Fur | Sale bans: California (2023), Israel (2021, traditional-dress exemption), many US cities; Switzerland import ban advancing; EU under consideration; farming bans in PL/SE/BG are not sale bans | US-CA, IL, CH (pending) | Us | `contains_animal_fur` + species; geo-block CA/IL; real-fur disclosure (US Fur Products Labeling Act, EU textile reg) | Medium [N] |
| 5 | Precious-metal claims ("gold", "silver", "925") | UK Hallmarking Act: unhallmarked items over the exemption weight (Ag 7.78 g, Au/Pd 1 g, Pt 0.5 g, by total metal weight) may not be described as precious metal; online Dealer's Notice; compulsory hallmarking also in IE, NL, FR, PT, CZ, PL, HU, AT, CY, CH (responsibility mark); US FTC Jewelry Guides | UK, IE, EU, CH, US | Us (the describer/seller) | `metal`, `fineness`, `metal_weight_g`, `plating`; **block bare "gold/silver" wording for fashion jewellery** | UK high [P/A]; other EU states UNVERIFIED |
| 6 | Sunglasses | EU: PPE Reg 2016/425 Cat. I, CE + EU DoC (EN ISO 12312-1:2022), and an EU-based responsible operator (Art. 4 of Market Surveillance Reg 2019/1020); UK the same (UKCA/CE); AU: mandatory standard AS/NZS 1067.1:2016 (from 01-Jul-2019) incl. lens category; US: FDA medical device (code HQY), drop-ball test 21 CFR 801.410, foreign establishment registration + US agent | EU, UK, AU, US (+CA, NZ unknown) | Manufacturer (merchant) + us as importer/seller | `is_sunglasses`, lens category, DoC/test report, FDA registration no. | Medium-high [P/A] |
| 7 | Heavy metals in jewellery & accessories | EU REACH Annex XVII: Ni release ≤0.5 µg/cm²/week (0.2 for piercing posts), Cd ≤100 mg/kg (entry 23), Pb ≤500 mg/kg (entry 63); US-CA H&SC 25214.1-.4 (adult jewellery, SB 647) + Prop 65; MN 325E.3892; CA (Canada) children's jewellery Pb 90 / Cd 130 mg/kg | EU, UK, CH, US, CA | Us (seller/importer); merchant supplies tests | Test report per material group (XRF/ICP), `contains_nickel_parts` | Medium [A/N]; EU figures consistent across sources but not checked in EUR-Lex |
| 8 | Leather Cr(VI) | REACH entry 47: ≤3 mg/kg in leather that touches skin (Reg 301/2014) | EU, UK (retained) | Us/merchant | Test report (EN ISO 17075) per leather supplier | High [P] |
| 9 | Counterfeit / IP | As seller and IOR we are the **direct** infringer (no hosting safe harbour); US 19 USC 1526(f) civil fines on the importer; JP: since 01-Oct-2022, an overseas business shipping fakes to a private buyer infringes (UNVERIFIED); EU 608/2013 small-consignment destruction; card schemes (Visa/Mastercard brand-protection programmes) | All | Us | Brand declaration (own brand / authorised reseller + evidence), blocked-brand list, image/keyword screening, notice and takedown | Principle high; specifics UNVERIFIED |
| 10 | Courier rules | DHL VN: ivory and CITES-prohibited animal products, loose stones and bullion prohibited; CITES App. II/III skins restricted (need DHL approval); jewellery/watches > EUR 10,000 restricted; counterfeit prohibited. FedEx: country lists include furs, ivory, jewellery; > USD 20,000 needs approval. UPS: furs, ivory prohibited; jewellery limits vary (USD 500 figure from a third party) | All | Us (shipper) | Courier rules per lane; cap jewellery declared value | Medium [P DHL; P/N FedEx, UPS] |
| 11 | IATA dangerous goods | Our goods are not dangerous goods. Watch for strong magnets (UN2807, rarely met by clasps), leather care kits (solvents, aerosols), LED shoes (batteries: out of scope) | All | Us (shipper) | Flag kits/add-ons | Background |
| 12 | Forced labour | US UFLPA (rebuttable presumption; cotton/apparel high priority; VN among the top countries for detentions); EU FLR 2024/3015 applies **14-Dec-2027** and covers online offers aimed at EU users; Canada tariff 9897.00.00 import ban + S-211 reporting; Mexico import ban (2023); UK MSA s54 (£36m); AU MSA (A$100m) | US, EU, CA, MX, UK, AU | IOR (us) for import bans; reporting only above turnover thresholds | Supplier map per SKU: fabric mill, yarn spinner, cotton origin; cotton-bearing flag | High on dates [A]; MX/CA details UNVERIFIED |
| 13 | GCC, LatAm, ZA labelling/conformity | KSA SABER (PC + SC), Arabic labels; BR Inmetro textile labelling; MX NOM-004-SE-2021, NOM-020-SCFI-1997, NOM-050-SCFI-2004, importer registry/RFC; CL textile labelling; ZA SANS 10011 | Later markets | IOR (us) or local importer | Fibre content, origin, care, importer ID in the local language | **UNVERIFIED** (not researched, no budget left) |
| 14 | Children's clothing | Drawstrings (EN 14682, 16 CFR 1120), flammability, CPSIA (lead, phthalates, CPC, tracking labels), AU nightwear standard, small parts | All | Us | **Recommend excluding at launch** | Background |

---

## 1. Materials across markets

### 1.1 CITES and exotic leathers

- **Species to screen.** The VN farm trade is mainly python (reticulated *Malayopython reticulatus* and Burmese *Python bivittatus*, both CITES App. II) and Siamese crocodile (*Crocodylus siamensis*, App. I). Captive-bred App. I from CITES-registered breeding operations is traded like App. II. A 2024 VN agriculture report says only crocodiles from farms registered with the CITES Secretariat can be exported, and that permit lead times and costs have grown [N vietnamagriculture.nongnghiep.vn]. Also:
  - stingray (most marine Dasyatidae are not listed; some freshwater *Potamotrygon* are App. III) — UNVERIFIED by species;
  - ostrich (App. I only for North African *S. c. camelus*; farmed southern African ostrich is not listed) — background;
  - lizard, karung (file snake), cobra (*Naja* spp. App. II);
  - jewellery materials: red/pink coral *Corallium* (App. III), black coral (App. II), giant clam shell (Tridacnidae, App. II), queen conch (App. II).
  - Ordinary mother-of-pearl from *Pinctada* pearl oysters and cultured pearls are generally not listed — background.
- **VN export.** Every commercial movement of python or crocodile leather products needs an origin-country CITES export permit, even a single handbag [N goodsacrossborders]. VN legal basis: Decree 06/2019/ND-CP, which replaced Decree 82/2006 [P leap.unep.org; WTO import-licensing copy of 82/2006]. The VN CITES Management Authority sits under the Ministry of Agriculture and Environment. Circular 116/2013/TT-BTC requires CITES permit data (source code "II-C") for the python-skin export-tax exemption [P luatvietnam/lawnet]. So VN customs does check permits on the export side.
- **Destination.** The EU (Reg. 338/97, Annex B) requires an **EU import permit** from the member state's Management Authority on top of the VN export permit for commercial imports — background, high confidence. Most other CITES parties need only the export permit for App. II, plus whatever their national law adds (AU: EPBC permit; US: see 1.2).
- **Personal effects.** The CITES personal-effects exemption covers items carried in the owner's luggage. It does not cover items bought online and shipped (CITES Res. Conf. 13.7) — background, high confidence. B2C parcels therefore count as commercial.
- **Enforcement reality.** A Canadian court fined an Ontario man for importing python and crocodile wallets and purses from Vietnam without permits [P canada.ca]. Express couriers treat CITES App. II/III skins as restricted, needing prior DHL approval [P DHL VN].
- **US extras.** Every wildlife import (CITES-listed or not, exotic leather included) needs a USFWS Form 3-177 declaration, filed through eDecs, and entry through a designated port unless a port-exception permit is held [P fws.gov forms; FedEx 3-177 guide]. Commercial importers also need a USFWS import/export licence (50 CFR 14.91) — detail UNVERIFIED, the eCFR fetch was blocked. **California Penal Code §653o** bans the sale of products from listed species. Python is believed to be listed; crocodile and alligator have a contested exemption — **UNVERIFIED, high-risk**. A 2024-era item also says some VN-farmed python species are prohibited in the US [N vietnamplus].
- **Recommendation.** At launch, ban CITES-listed materials from the catalogue (python, crocodile, cobra, coral, giant clam) and require a species declaration for any "exotic" leather. Add CITES as a separate feature later: per-shipment permits, EU import permits, USFWS licence, carrier pre-approval. Selling one bag per parcel makes per-shipment permits uneconomic.

### 1.2 Other wildlife: feathers, shells, horn, bone

- US: any wildlife part (feathers, shells, horn, bone, including non-endangered species) triggers USFWS 3-177 and the licence. Domesticated species such as cattle, sheep, goat and farmed pig leather are exempt (50 CFR 14.92 domestic-animal exclusion — background).
- Migratory bird feathers (US MBTA) are prohibited. Peacock (domesticated) feathers are OK.
- Ivory, rhino horn, tortoiseshell (hawksbill), whale bone and shark products: **hard block** everywhere. Mammoth ivory is also banned in several US states (NY, NJ, CA, HI) — background.

### 1.3 Fur

- California's ban on the sale of new fur has applied statewide since 2023 [N]. Israel banned fur sales for fashion in 2021, with an exemption for traditional dress [N]. US cities with bans include West Hollywood, San Francisco, Ann Arbor, Wellesley and other MA towns, and Chicago (ordinance expected 2025 — UNVERIFIED whether passed) [N aldf.org, humaneworld.org].
- Switzerland is moving towards a fur import ban, not yet enacted. The EU is considering a ban but has no measure yet. Poland, Sweden and Bulgaria ended fur *farming* in 2025, which does not affect sales [N furfreealliance.com 2025]. A UK sales ban was only at the call-for-evidence stage — status UNVERIFIED.
- Labelling: the US Fur Products Labeling Act requires species name and country of origin, and the EU textile regulation requires "contains non-textile parts of animal origin". Faux fur must not be described in a way that implies real fur.
- Recommendation: block real fur (including trims such as rabbit pom-poms and fox collars) at launch. Have merchants declare "faux".

### 1.4 Precious metals, hallmarking and gemstones

- **UK.** The Hallmarking Act 1973 makes it an offence, in the course of trade, to describe an unhallmarked article as gold, silver, platinum or palladium [P gov.uk]. The exemption weights count total metal including base metal: silver 7.78 g, gold and palladium 1 g, platinum 0.5 g [P gov.uk hallmarking guidance notes]. Online sellers must display the Dealer's Notice (version A or B; the new version has been compulsory since 01-May-2020) [P gov.uk]. The Assay Office's view is that internet sales to UK buyers fall under the law [A assayoffice.co.uk]. A Convention (CCM) hallmark is accepted. "Gold-plated" or "silver-plated" on base metal is permitted if it is accurate.
- **Ireland** (Hallmarking Act 1981), **Netherlands** (Waarborgwet), **France**, **Portugal**, **Czechia**, **Poland**, **Hungary**, **Austria** and **Cyprus** have compulsory hallmarking above weight thresholds. **Switzerland** requires a responsibility mark plus fineness, with an optional official hallmark. Other EU states (DE, IT, ES and others) only need a fineness stamp that matches reality, under unfair-commercial-practices law. **UNVERIFIED thresholds.**
- **US:** FTC Jewelry Guides (16 CFR Part 23) and the National Gold and Silver Stamping Act (fineness tolerances; the maker's trademark must accompany a quality mark). Gemstones: "lab-created", "simulated" or "imitation" must be disclosed, and so must treatments. SG, MY, HK: SG has no compulsory hallmarking (Consumer Protection (Trade Descriptions) Act); HK uses a gold-fineness standard; MY has none — UNVERIFIED.
- **Recommendation.** The catalogue is fashion jewellery, so do not allow bare precious-metal words. Allow "gold-tone", "gold-plated (brass base)" and "925 sterling silver" only where the merchant gives fineness and weight. For UK and IE, block "silver/gold" descriptions of items over the exemption weight unless they are hallmarked. Real gemstones and pearls need a claim plus evidence. Default description: "simulated", "glass", "crystal".

### 1.5 Sunglasses (the hardest product category in scope)

- **EU.** PPE Regulation 2016/425, Category I (minimal risk): manufacturer self-declaration (Module A), CE mark on the frame and packaging, an EU DoC, and instructions in the local language. Harmonised standard EN ISO 12312-1:2022 (the 2013+A1 edition is withdrawn) [A compliancegate, DIN CERTCO]. The PPE Regulation is listed in Art. 4(5) of Market Surveillance Regulation 2019/1020, so an **EU-established economic operator** (manufacturer, importer, authorised representative or fulfilment provider) must exist, with its name and contact on the product, packaging or documents — background, high confidence. If we sell from a Dutch BV and act as importer, that operator is us; a Singapore entity would need an EU authorised representative.
- **UK:** the same retained regime; CE marking is still accepted in GB — background.
- **AU:** the Consumer Goods (Sunglasses and Fashion Spectacles) Safety Standard is mandatory and references AS/NZS 1067.1:2016 from 01-Jul-2019 (lens category labelling, transmittance, and "not suitable for driving" warnings for some categories) [P legislation.gov.au F2017L01391; ACCC]. The supplier into AU, which is us as seller, must comply. Penalties apply under the ACL.
- **NZ:** no mandatory sunglasses standard is believed to exist — UNVERIFIED.
- **US.** Non-prescription sunglasses are an FDA medical device (product code HQY, Class I) [A diaztradelaw]. Every lens must pass the drop-ball impact test (21 CFR 801.410: 5/8-inch steel ball dropped from 50 inches), with batch sampling and records kept 3 years [P eCFR, FDA guidance]. Shipments can be held without a drop-ball certificate (Import Alert 86-07) [A]. Foreign manufacturers must register the establishment and name a US agent (21 CFR 807) [A]. Sources disagree on whether HQY is exempt from 510(k); it is believed to be exempt — UNVERIFIED. US de minimis ended in 2025, so every parcel is now a formal or informal entry and FDA data will be requested.
- **CA, JP, KR:** not checked. KR KC safety marking for sunglasses (UV): UNVERIFIED.
- **Recommendation.** Treat sunglasses as a gated category. Before listing, require from the merchant:
  - EU DoC + EN ISO 12312-1 test report;
  - AS/NZS 1067.1 test report;
  - FDA drop-ball certificate + FDA registration number.

  If the merchant has none, geo-block the US, EU, UK and AU for that SKU.

### 1.6 Heavy metals and chemicals in jewellery and leather

- **EU/UK REACH Annex XVII** [A/N, figures consistent across sources]:
  - nickel release ≤ 0.5 µg/cm²/week for prolonged skin contact; ≤ 0.2 for piercing posts (entry 27);
  - cadmium ≤ 0.01 % (100 mg/kg) in metal parts of jewellery (entry 23);
  - lead ≤ 0.05 % (500 mg/kg) in jewellery parts (entry 63);
  - Cr(VI) < 3 mg/kg in leather in skin contact (entry 47, Reg 301/2014, test EN ISO 17075) [P legislation.gov.uk/eur/2014/301].
  - Adjacent rules, background: azo dyes (entry 43, textiles and leather), PAHs in rubber/plastic parts (entry 50), dimethylfumarate, and the PFHxA restriction for consumer textiles (Reg 2024/2462), which is believed to apply from 10-Oct-2026 — **UNVERIFIED, check, likely another researcher's slice**.
- **US.**
  - California H&SC §25214.1-.4: lead and cadmium limits for **adult** and children's jewellery, tightened by SB 647. Manufacturers and suppliers must certify compliance, and a knowing violation is a misdemeanour [P DTSC fact sheet; A Steptoe].
  - Prop 65: lead, cadmium and phthalates (PVC bags) need a "clear and reasonable" warning online and on the package. Private enforcers use it heavily against bags and jewellery — background.
  - Minnesota §325E.3892: lead 90 ppm in covered (children's) products, amended in 2025 [P revisor.mn.gov].
  - Other states with cadmium-in-children's-jewellery laws: CT, IL, WA, RI, NY — background.
  - CPSIA for children's jewellery.
- **Canada:** Children's Jewellery Regulations SOR/2018-82, total lead 90 mg/kg and total cadmium 130 mg/kg [P gazette.gc.ca]; Surface Coating Materials Regulations. No federal adult-jewellery limit is believed to exist — UNVERIFIED.
- **AU/NZ, SEA, JP, KR:** no adult-jewellery heavy-metals law was found or researched. KR KC "safety standard for accessories" (Ni, Cd, Pb) is believed to exist under the Electrical Appliances and Household Goods Safety Act — UNVERIFIED.
- **Data per SKU:** for each component material, a lab report (XRF screen + nickel-release test EN 1811) within 12-24 months. Flag `skin_contact_metal`, `pierced_post`.

## 2. Counterfeits and IP

UNVERIFIED unless noted: the search budget ran out here.

- **Our liability.** As MoR we are not a marketplace or host. We offer, sell, import (as IOR) and invoice in our own name. That is direct "use in the course of trade" of the mark under EU TMR Art. 9, UK TMA s10, US Lanham Act §32 and JP Trademark Act Art. 2(3). Notice-and-takedown safe harbours (EU DSA Art. 6, US DMCA §512 for copyright) **do not shield a seller**. CJEU *Louboutin v Amazon* (C-148/21, 2022) found even a marketplace can be the user where it presents offers as its own, so an MoR certainly is.
  - US: 19 USC 1526(f) fines the importer of counterfeit-mark goods up to the MSRP of genuine goods, so DDP-as-IOR makes this direct exposure.
  - Japan: amended Trademark Act and Design Act, in force 01-Oct-2022. An overseas business that sends counterfeits to a private buyer in Japan "imports" them, so customs can seize even single personal-use parcels.
  - EU: Reg 608/2013 small-consignment procedure; goods destroyed without a court case.
  - KR, US, EU and JP customs are among the most active against apparel, bags and shoes, and DHL VN lists shoes, clothing and bags as commonly copied goods [P DHL VN].
- **Payments risk.** Visa (VIRP) and Mastercard (BRAM) brand-protection programmes fine acquirers or merchants for counterfeit sales. As MoR **we** are the card merchant, so counterfeits can cost us acceptance.
- **Dupes and lookalikes.** Even without a copied logo, risk comes from:
  - registered and unregistered designs: EU UCD gives 3 years' protection from first EU disclosure, plus RCD (Reg 2024/2822 reform); UK supplementary unregistered design (2024-);
  - US trade dress and design patents (common for bag shapes and shoe soles);
  - JP Unfair Competition Prevention Act Art. 2(1)(iii) "dead copy" (3 years);
  - copyright in prints and graphics.

  "Dupe" marketing language ("inspired by [Brand]", "[Brand] dupe") is itself a trademark use and invites claims.
- **Controls we need:**
  1. Merchant onboarding attestation: own brand (with trademark registration or application numbers, or a statement that the brand is unregistered), or authorised reseller (brand authorisation letter, distribution agreement, invoices from the brand or authorised distributor). No third-party brands without evidence.
  2. A blocked-brand list and keyword filter on titles and descriptions ("dupe", "inspired by", "replica", "AAA", "1:1", "mirror quality", luxury brand names); image similarity or logo detection on listings.
  3. Notice and takedown: a published IP-complaint channel, SLA-bound removal, counter-notice, a repeat-infringer policy (suspend after N strikes), and holding back the merchant's payout for disputed orders.
  4. Contract: IP warranty, indemnity, a rolling reserve, our right to destroy seized goods and charge the merchant, and audit or test buys.
  5. Record keeping: link each order to the SKU, merchant and evidence, so we can answer customs and rights-holder letters.
- **Grey market.** Genuine branded goods bought from authorised factories' overruns are counterfeits in law in most markets. Genuine goods first sold outside the EEA cannot be imported into the EU without consent (no international exhaustion). The US Lever rule and the JP parallel-import doctrine are more permissive but not safe. Treat any third-party brand as high risk.

## 3. Couriers and IATA

- **DHL Express Vietnam** [P mydhl.express.dhl/vn]:
  - Prohibited: ivory and shark fin; animal by-products where CITES or local law prohibit them; bullion; loose precious and semi-precious stones; counterfeits in breach of IPR.
  - Restricted, needing DHL agreement: CITES App. II/III skins; jewellery and watches > EUR 10,000; fashion goods > EUR 1,000,000.
  - Other DHL country pages are stricter (UK: no jewellery; IT: EUR 1,000 per item).
- **FedEx** [P fedex.com PDFs, July 2023]: shipments over USD 20,000 need approval; country-specific lists include furs, ivory, jewellery and costume jewellery. Third-party summaries list fine jewellery and precious metals as prohibited [N].
- **UPS** [P ups.com]: furs and ivory prohibited; on some country pages jewellery and loose stones are prohibited or limited ("unusual value"). A USD 500 jewellery limit is reported by a third party — UNVERIFIED.
- **Implication.** Fashion jewellery is accepted, but each carrier contract must confirm costume jewellery acceptance per lane (some country lists name "costume jewellery"). Declared value caps per parcel. No CITES skins without carrier pre-approval. No real fur (FedEx/UPS lists).
- **IATA DGR.** Apparel, footwear, bags, jewellery and sunglasses are not dangerous goods.
  - Magnetic clasps are far below the UN2807 threshold — background.
  - Exclude add-ons: leather care sprays and conditioners (aerosols, flammable), perfume samples, LED or heated items (batteries), silica-gel packs (OK), dry ice (n/a).

## 4. Forced labour and supply-chain due diligence

- **US UFLPA** (since 21-Jun-2022): a rebuttable presumption that goods made wholly or partly in Xinjiang, or by entities on the UFLPA Entity List, are made with forced labour. Cotton, apparel and yarn at any stage are high priority, so a garment sewn in VN from Xinjiang-cotton yarn is caught [N beancount 2026, everstream].
  - Vietnam is among the top origins of detentions; FDRA reports more VN textile/apparel detentions than China (about $45m) [A FDRA 2025].
  - In FY2025 CBP detained more than 17,600 shipments in total and released very few (about 6.5 % in FY2025 per one analysis) [N/A].
  - The **IOR** must prove traceability from cotton bale to garment: purchase orders, invoices, packing lists and production records for the spinner, mill and factory, plus transport documents. Isotope testing (Oritain) is increasingly used.
  - Section 307 withhold-release orders (WROs) also apply.
  - De minimis is gone, so parcels are entries and can be detained.
- **EU Forced Labour Regulation 2024/3015:** in force 13-Dec-2024, **applies 14-Dec-2027**, every company size, and it covers products offered online or at a distance to EU end users. Commission guidelines were due 14-Jun-2026; member-state penalties are due 14-Dec-2026 [A Fieldfisher, Squire Patton Boggs, IHK]. It works by investigation and removal from the market, not a presumption.
- **Canada:** Customs Tariff 9897.00.00 bans imports made with forced labour (since 2020). The Fighting Against Forced Labour and Child Labour in Supply Chains Act (S-211) requires annual reports from 2024 from entities that import into Canada and meet thresholds — UNVERIFIED, believed to be 2 of: C$20m assets, C$40m revenue, 250 employees.
- **Mexico:** import ban on goods made with forced labour since 2023, complaint-driven — UNVERIFIED.
- **UK** MSA 2015 s54: statement required at £36m global turnover where the company carries on business in the UK. **AU** Modern Slavery Act 2018: statement required at A$100m consolidated revenue — background. Not us at launch.
- **EU CSDDD** after Omnibus I (agreed late 2025): thresholds about 5,000 employees and €1.5bn turnover, application pushed to around 2029 — UNVERIFIED. **Not us.**
- **EUDR:** covers cattle hides and leather (HS 4101/4104/4107) but not finished bags and shoes (HS 42/64) — background. Application date after the 2025 delays is UNVERIFIED.
- **Data per SKU:** fibre composition, cotton content flag, and the supplier chain (fabric mill, yarn spinner, cotton origin country). For cotton-bearing SKUs to the US, a document pack (supplier declarations + mill and spinner certificates; optional Oritain / Better Cotton / OEKO-TEX), plus a merchant attestation and our right to audit.

## 5. Rest-of-world product rules — **UNVERIFIED, not researched (no search budget left)**

- **Saudi Arabia (SABER/SASO):**
  - Textiles and footwear fall under SASO technical regulations; commercial shipments need a SABER Product Certificate (PC) + Shipment Certificate (SC).
  - Personal or low-value courier shipments may be exempt — exemption threshold unknown.
  - Arabic labelling of origin and fibre content is required.
- **UAE:** ECAS/EQM (MoIAT) conformity is mainly for regulated categories. Textile fibre-content labelling is believed to be required in Arabic/English. Other GCC states run their own GSO-based regimes.
- **Brazil:** the Conmetro textile-labelling resolution (Res. 02/2008) was replaced by **Inmetro Portaria 118/2021** — believed, verify. Requires fibre content in Portuguese, care symbols, size, country of origin and importer CNPJ. Footwear material labelling is under Inmetro rules. B2C cross-border goes through the Remessa Conforme regime (tax); labelling enforcement on parcels is believed to be low.
- **Mexico:**
  - Textile labelling: NOM-004-SE-2021 (Spanish fibre content, care, size, origin, importer name and RFC).
  - Footwear and leather: NOM-020-SCFI-1997.
  - General: NOM-050-SCFI-2004.
  - Commercial imports need registration in the Padrón de Importadores (RFC). Courier B2C shipments go through the simplified courier regime, where NOM labelling may still be demanded.
  - VN and MX are both CPTPP members (tariff effect is another slice's topic).
- **Chile:** a textile fibre-content labelling regulation applies — details unknown.
- **South Africa:** SANS 10011 (labelling of textiles, clothing, footwear and leather goods) is referenced under the Consumer Protection Act regulations: fibre content, origin, care.
- **Common pattern:** every RoW market needs fibre content, origin, care and importer ID **in the local language, on the article**. Build the label data model so it can render per-market sew-in or swing tags, and have merchants apply them before dispatch.

## 6. Children's clothing (flag)

Not researched. The regimes are heavier everywhere:

- US CPSIA: third-party testing, Children's Product Certificate, tracking labels, lead and phthalates, drawstrings 16 CFR 1120, sleepwear flammability;
- EU: EN 14682 drawstrings, GPSR;
- AU: children's nightwear standard; UK: the same as the EU;
- small-parts and choking rules apply to buttons and decorations.

**Recommend excluding ages 0-14 at launch.**

## 7. Catalogue screening fields (proposal)

`materials[]` (per component: material, animal species scientific name, `cites_appendix`, `source_code`, faux/real), `contains_fur`, `contains_wildlife`, `metal_parts[]` (alloy, plating, `fineness`, `weight_g`, `skin_contact`, `pierced_post`), `gemstone_claim` (none/simulated/natural/lab-grown + evidence), `is_sunglasses` + `lens_category` + certificates, `brand` + `brand_relationship` (own/authorised/none) + evidence, `cotton_content` + supply-chain chain, `target_age`, `test_reports[]` (lab, standard, date, scope), `per_market_eligibility` computed from all of the above.

## Sources

- Vietnam agriculture, crocodile exports: https://vietnamagriculture.nongnghiep.vn/big-opportunity-for-crocodile-exports-d401381.html [N]
- CITES permit guide: https://goodsacrossborders.com/guides/cites-permit-process [N]
- Canada exotic leather enforcement: https://www.canada.ca/en/environment-climate-change/services/environmental-enforcement/notifications/import-exotic-leather-ontario-man.html [P]
- Circular 116/2013: https://english.luatvietnam.vn/circular-no-116-2013-tt-btc-dated-august-20-2013-of-the-ministry-of-finance-on-guiding-the-exemption-of-export-tax-for-products-from-python-skin-hav-80746-doc1.html [P]
- Decree 06/2019: https://leap.unep.org/en/countries/vn/national-legislation/decree-no062019nd-cp-management-endangered-precious-and-rare [P]
- Python species prohibited in US: https://en.vietnamplus.vn/python-species-being-bred-in-vn-prohibited-in-us-post35782.vnp [N]
- USFWS 3-177: https://www.fws.gov/le/declaration-form-3-177.html ; https://www.fedex.com/content/dam/fedex/apac-asia-pacific/downloads/fedex-usfws3177-en-cn.pdf [P]
- UK hallmarking: https://www.gov.uk/guidance/hallmarking-how-businesses-can-comply-with-the-law ; https://assets.publishing.service.gov.uk/media/63a333b9d3bf7f37654767e0/hallmarking-guidance-notes.pdf ; https://www.assayoffice.co.uk/news/internet-sales-could-see-you-breaking-the-law [P/A]
- AU sunglasses: https://legislation.gov.au/F2017L01391/asmade/2017-10-25/es/original/pdf ; https://consultation.accc.gov.au/product-safety/standard-review-sunglasses/ [P]
- EU sunglasses: https://www.compliancegate.com/european-union-sunglasses-eyewear-regulations/ ; https://dincertco.de/din-certco/en/main-navigation/products-and-services/certification-of-products/eye-protection/category-i-products/ [A]
- FDA drop ball: https://www.law.cornell.edu/cfr/text/21/801.410 ; https://www.hhs.gov/guidance/sites/default/files/hhs-guidance-documents/FDA/Impact-Resistant-Lenses--Questions-and-Answers---Guidance-for-Industry-and-FDA-Staff.pdf ; https://diaztradelaw.com/know-sunglasses-regulated-fda-medical-devices/ [P/A]
- REACH nickel: https://echa.europa.eu/documents/10162/13641/nickel_restriction_prolonged_contact_skin_en.pdf/b6f35357-da40-4a04-8085-fe42f6f543ab [P]; https://www.compliancegate.com/reach-annex-xvii-substances-list/ [A]
- REACH Cr(VI): https://www.legislation.gov.uk/eur/2014/301 [P]
- California jewellery: https://dtsc.ca.gov/californias-metal-containing-jewelry-law-fact-sheet [P]; https://www.steptoe.com/en/news-publications/california-adopts-stricter-limits-on-lead-and-cadmium-in-jewelry.html [A]
- Minnesota: https://www.revisor.mn.gov/statutes/2025/cite/325E.3892/subd/325E.3892.1 [P]
- Canada children's jewellery: https://gazette.gc.ca/rp-pr/p2/2018/2018-05-02/html/sor-dors82-eng.html [P]
- Fur: https://aldf.org/article/protecting-animals-through-local-legislation/fur-sale-bans/ ; https://www.furfreealliance.com/2025-major-wins-for-our-global-fur-free-movement ; https://www.humaneworld.org/en/blog/switzerland-becomes-first-country-ban-fur-imports [N]
- DHL VN: https://mydhl.express.dhl/vn/en/help-and-support/shipping-advice/what-can-i-ship/prohibited-items.html [P]
- FedEx: https://www.fedex.com/content/dam/fedex/us-united-states/shipping/fedex-international-connect-country-region-specific-prohibited-and-restricted-items.pdf [P]
- UPS: https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/prohibited-items [P]
- UFLPA: https://fdra.org/wp-content/uploads/2025/01/FDRA-Customs-Report-January-2025.pdf [A]; https://www.everstream.ai/articles/uflpa-enforcement-detains-additional-imports/ [N]; https://www.just-style.com/news/vietnam-beats-china-for-most-uflpa-detained-shipments-in-2023/ [N]
- EU FLR: https://www.fieldfisher.com/en/locations/germany/insights/eu-forced-labour-regulation ; https://www.squirepattonboggs.com/en/insights/publications/2025/01/eu-forced-labour-regulation-what-you-need-to-know ; https://transition-pathways.europa.eu/retail/legislation/forced-labour-regulation-0 [A/P]
