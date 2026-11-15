# Existing tools

A quick look at free chemical inventory tools, done on 15 November 2026 from public repository pages. It is not a complete survey, and licenses are as each page listed them that day. Check the repository before you rely on a license.

| Project | Aimed at | License listed |
| --- | --- | --- |
| [open_enventory](https://github.com/ICIQ-DMP/open-enventory) (see also the [US fork](https://github.com/khoivan88/open_enventory-modified_for_US)) | Labs: inventory and electronic lab notebook, PHP | AGPL (US fork listing) |
| [gochimitheque](https://github.com/tbellembois/gochimitheque) | Labs, Go | GPL v3 |
| [Pretzel](https://github.com/GetPretzel/Pretzel) | Hazardous chemical stock, desktop | GPL v3 |
| [TuDa.CIMS](https://github.com/projectcims/tuda.cims) | University chemical stock | MIT |
| [lab-inventory](https://github.com/hitaishi2222/lab-inventory) | Lab equipment and consumables, Docker | MIT |
| [cheminv](https://github.com/tmorrell/cheminv) | Lab chemicals by location, MySQL | CC BY-NC-SA 3.0 |

Also: the Irish Health and Safety Authority publishes a static [chemical inventory template](https://www.hsa.ie/eng/publications_and_forms/publications/chemical_and_hazardous_substances/chemical_inventory_template/) as a spreadsheet.

## Why a new, small one

Most of the above are built for labs and need a database server, a runtime or a container. The aim here is one cupboard in a hall, club or small shop: install nothing, open it on a phone, attach the photo and the safety data sheet, see what is out of date.

## What was reused

No code. The ideas (inventory with expiry, safety data sheet per product, a disposal record) are common to all of them. GPL, AGPL and non-commercial licenses would not allow copying code into an MIT project, so nothing was copied. If you want to reuse something from the MIT-licensed projects, keep their copyright notice.
