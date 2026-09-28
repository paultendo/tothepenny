# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Country names (ISO 3166-1 English short names, with the forms addresses commonly print), for recognising the last
line of an address outside the UK. A name missing here only means an overseas address is not read; it never makes a
line an address."""

COUNTRIES = frozenset(name.upper() for name in """
Afghanistan|Aland Islands|Albania|Algeria|American Samoa|Andorra|Angola|Anguilla|Antarctica|Antigua and Barbuda|Argentina|
Armenia|Aruba|Australia|Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|Benin|Bermuda|Bhutan|
Bolivia|Bonaire|Bosnia and Herzegovina|Botswana|Bouvet Island|Brazil|British Indian Ocean Territory|Brunei|Brunei Darussalam|
Bulgaria|Burkina Faso|Burundi|Cabo Verde|Cape Verde|Cambodia|Cameroon|Canada|Cayman Islands|Central African Republic|Chad|
Chile|China|Christmas Island|Cocos Islands|Colombia|Comoros|Congo|Democratic Republic of the Congo|Cook Islands|Costa Rica|
Cote d'Ivoire|Ivory Coast|Croatia|Cuba|Curacao|Cyprus|Czechia|Czech Republic|Denmark|Djibouti|Dominica|Dominican Republic|
Ecuador|Egypt|El Salvador|Equatorial Guinea|Eritrea|Estonia|Eswatini|Ethiopia|Falkland Islands|Faroe Islands|Fiji|Finland|
France|French Guiana|French Polynesia|Gabon|Gambia|The Gambia|Georgia|Germany|Ghana|Gibraltar|Greece|Greenland|Grenada|
Guadeloupe|Guam|Guatemala|Guernsey|Guinea|Guinea-Bissau|Guyana|Haiti|Holy See|Honduras|Hong Kong|Hungary|Iceland|India|
Indonesia|Iran|Iraq|Ireland|Republic of Ireland|Eire|Isle of Man|Israel|Italy|Jamaica|Japan|Jersey|Jordan|Kazakhstan|Kenya|
Kiribati|North Korea|South Korea|Korea|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|Liberia|Libya|Liechtenstein|
Lithuania|Luxembourg|Macao|Macau|Madagascar|Malawi|Malaysia|Maldives|Mali|Malta|Marshall Islands|Martinique|Mauritania|
Mauritius|Mayotte|Mexico|Micronesia|Moldova|Monaco|Mongolia|Montenegro|Montserrat|Morocco|Mozambique|Myanmar|Namibia|Nauru|
Nepal|Netherlands|The Netherlands|New Caledonia|New Zealand|Nicaragua|Niger|Nigeria|Niue|Norfolk Island|North Macedonia|
Northern Mariana Islands|Norway|Oman|Pakistan|Palau|Palestine|Panama|Papua New Guinea|Paraguay|Peru|Philippines|Pitcairn|
Poland|Portugal|Puerto Rico|Qatar|Reunion|Romania|Russia|Russian Federation|Rwanda|Saint Barthelemy|Saint Helena|
Saint Kitts and Nevis|Saint Lucia|Saint Martin|Saint Pierre and Miquelon|Saint Vincent and the Grenadines|Samoa|San Marino|
Sao Tome and Principe|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|Sint Maarten|Slovakia|Slovenia|
Solomon Islands|Somalia|South Africa|South Sudan|Spain|Sri Lanka|Sudan|Suriname|Svalbard and Jan Mayen|Sweden|Switzerland|
Syria|Taiwan|Tajikistan|Tanzania|Thailand|Timor-Leste|Togo|Tokelau|Tonga|Trinidad and Tobago|Tunisia|Turkey|Turkiye|
Turkmenistan|Turks and Caicos Islands|Tuvalu|Uganda|Ukraine|United Arab Emirates|UAE|United States|United States of America|
USA|Uruguay|Uzbekistan|Vanuatu|Venezuela|Vietnam|Viet Nam|British Virgin Islands|US Virgin Islands|Wallis and Futuna|
Western Sahara|Yemen|Zambia|Zimbabwe
""".replace("\n", "").split("|") if name.strip())
