# Vendored age testkit vectors (comms v0.3 Task B22)

Source: https://github.com/C2SP/CCTV, directory `age/testdata`, commit `4448f2097b2daa812c91a26141f9f36c2096b9ca`.

Selection: every vector whose identities are all X25519 (`AGE-SECRET-KEY-1…`) and which is neither armored nor passphrase-based: the subset `comms.core.backup.age` implements. Files are byte-identical to the source; `tests/core/backup/test_age_vectors.py` re-checks each SHA-256 below.

| File | SHA-256 |
|---|---|
| `header_crlf` | `a152ced12c1a52249d218b43b19d34267f9cd712113cc2e8098c92e267702c3d` |
| `hmac_bad` | `07b287717c1cac4e99b0389d9aa7f94efddd6e3f5faa95963684f4d0abca3561` |
| `hmac_extra_space` | `f98b62cd56628b844a46c75eac9e14aed54ab96ac37f4d273800c5ab58a18dc5` |
| `hmac_garbage` | `0e2bcfc4d98bc286a01b9f3a69a71ed50b481f2ee0a20416ea7c1613cdaed10a` |
| `hmac_missing` | `40503e56cf692a67c295d95e0c3f02a350c26398016e9f0d59723f7476721b13` |
| `hmac_no_space` | `6497b5cb51a58b4382d7e9419afacfd70df82d5e1ea36564c5da0e5ccfa300ad` |
| `hmac_not_canonical` | `215c684fb5926db5a833de5b01d6b138b58f5838378d1e2b1859c4f9c9e4c81a` |
| `hmac_trailing_space` | `daf7151c8a5f6f48af641f4e0b3cdb41cb5876dbe0e4f6f24a3a4d0a452f7eb6` |
| `hmac_truncated` | `574748f519eddde3f8f7fd4eef8691d393d726d63b322e8c67f2377ea39b0841` |
| `stanza_bad_start` | `e2ba22a45e0913065e920629ba6f6a06e04169e2fe5f49579c769893e46d71de` |
| `stanza_base64_padding` | `7bf0dbb73084606ab26cd7a06b9fccc09b6f57b9cdd4b646f8b7e00c2402334e` |
| `stanza_empty_argument` | `89ea531b7224b5e6dac6b55cb365e990c7a8caa6bba31840a19bb2f74e078497` |
| `stanza_empty_body` | `2c2fd1650f43d4d347bfbd729b05bff56e0ba30dfebc0625d461b42c12bb2552` |
| `stanza_empty_last_line` | `f5760b7b3d27ac60c4390adab63f2387fec9a472dc71d68a94031e28174b6f14` |
| `stanza_invalid_character` | `fc50cb20219d50837086cb295832a28a52feca62e142cca3b4aeda3ead50e954` |
| `stanza_long_line` | `488be43646e7668cdc1db3260e0f84fd7b71ee40d797d22530d237b8c90d37b7` |
| `stanza_missing_body` | `2eb67c8cb24fcbd9dc37b9853ae1ddc0dac40ac17be8f7893909921a7bfefb2c` |
| `stanza_missing_final_line` | `4dbbba0f136f577469141a5010e8ea474c9c532ab66187e63b6aac9bd23bc52a` |
| `stanza_multiple_short_lines` | `e5146580aadb6942543acfb975ef2964f4a8a5eb2fd770c40d7da7b4a95f8834` |
| `stanza_no_arguments` | `730062e37e6fa0c0e1de5f8cee90662f61da99b7bb1a4996847a68afefaf8a1f` |
| `stanza_not_canonical` | `e2516017cdfa589e9ebc18c1646664d375c56e6f594191ae814da614add0a9f4` |
| `stanza_spurious_cr` | `0a58d23cc4398edf6a16c034c4a61da2e2006e4010377654ad50017468baa39d` |
| `stanza_valid_characters` | `115085567f8719b20e0903973873c20acae854b48975567f73cd15d29bd93a4d` |
| `stream_257_chunks` | `3ffc51d9314c11f84d049ad627d031ab8407abc1199df29144861fc6c4da92b8` |
| `stream_257_chunks_full` | `c55d0a954b0b25f13874734e25ca863a19e1b695af8dad5e3853176fbe2298c2` |
| `stream_258_chunks` | `0aff2b07b9cb6575863aa6b03ea78e40a49dc17ffa3bcb452a74c5bc7cd46ea6` |
| `stream_bad_tag` | `b302d219368735ca621d8237a49906831b0aa0f6116c05a1863aba18c421d9fe` |
| `stream_bad_tag_second_chunk` | `2763f46ed2ad05eace35a23084f14493b4593e03a85f8cbe15c4e608e2aaa324` |
| `stream_bad_tag_second_chunk_full` | `30e7a10c2725ff919e5d471be8022084485cc9eb447ab86bd7f7a6a3fb9dab08` |
| `stream_empty_payload` | `c04491b616be7f9c72f0dfaaf2b0b7ff0a6a716d0afd37d96b1dfc1800367762` |
| `stream_last_chunk_empty` | `4810c276566fe9977021a15b4e739bea2319ad5ddf0847f1dfba6fc1556d582a` |
| `stream_last_chunk_full` | `2dd3be61247bc2284cdac2df1cce6a18db0e00574157f19439e3c228245bfccd` |
| `stream_last_chunk_full_second` | `51008a18664460b97c6c621d71da607b09960141d08286a6b82e7a6818cc9efa` |
| `stream_missing_tag` | `51f96aba6a55c0c7634490a0b76ac53cfef383c44130f01de0b20cf0cad5ffcc` |
| `stream_no_chunks` | `506e93c795d1cab3cec64d6789f7fe45be545f10bff0b8a4d35d2472c58b5bd1` |
| `stream_no_final` | `22221d2161e2d579479f11a02061d30fa2004c3e55f256e3e7c33c4b1b8cf4b7` |
| `stream_no_final_full` | `23bce401fd7f428fa4b24d45c5054fef4cf3462a69047193032a17a2a043604c` |
| `stream_no_final_two_chunks` | `0e1b1ae57c213c738f8ecb5367bd65d894e7ce78208cdee1ffef51d220d9d222` |
| `stream_no_final_two_chunks_full` | `060b80ffaf5ae85aae6cf13cfcf0210927ab87cd63b03c20799d1c304261f27d` |
| `stream_no_nonce` | `c849c71e932e72e87c576610ce2321ca80bd6500920c34c65230296c9fb7bcad` |
| `stream_short_chunk` | `0c7b3c918f717c8fc047143ddcd244937a94aa3d9d81115fbb7c429991656d5b` |
| `stream_short_nonce` | `89ac7c59680477968325e3e0ffb67973743ad63c42d601685f7d7513a6c2ff32` |
| `stream_short_second_chunk` | `6c719858e51bb460ce192bebff1e9a60c62296976c6a616b84a8b0dd1aae951e` |
| `stream_three_chunks` | `65228acff4093f43666daca36557be239315153dfce50730ff7a7c79fbbb7d9c` |
| `stream_trailing_garbage_long` | `a019543f7c6d36a193e2f84ee348735e9e964f8785fdd5219e9cadef51211fc9` |
| `stream_trailing_garbage_short` | `42ab8680743ba4bdae15a991ce53a6ecac747d269cced7ebc85f7e3ceaec5a01` |
| `stream_two_chunks` | `51008a18664460b97c6c621d71da607b09960141d08286a6b82e7a6818cc9efa` |
| `stream_two_final_chunks` | `f29f4769f00b3e07fdf510b15e961d45b82137e86fe0282bb5e53a82a82ee1fa` |
| `stream_two_final_chunks_full` | `faacbff4a2d17dad128ec4f2e527411a613ed304c45798edc7d7a37b70b14306` |
| `stream_two_final_chunks_second` | `10d2df215cb75721451d956dacd8e48aa67889dc16d651eb03fa0eee9db29ee2` |
| `stream_two_final_chunks_short` | `481f70693cce27ae33a9ab1b5721dee652d218703efc0c37078c9e066cae24a3` |
| `version_unsupported` | `a13d6b218009563a1190ae5a19184a7325238903ec68d4ffe8593497ef4d7523` |
| `x25519` | `b8ae31af64f6a16718233844b77f117663753157c57238c8a30f0cdb4f1f434a` |
| `x25519_bad_tag` | `fb1a75b536696818855ec4ef82fabbd81cdf726a480565e88cc19152c7530c37` |
| `x25519_extra_argument` | `5a48e0ab9c2c8ff137a438c6201f01e9334729b9501a3d4e8baa788fe209f78f` |
| `x25519_grease` | `9d7f37bae2cc3932b8675e60efc9e27d56f902d6c1bab2a74d8452c39fc88f9e` |
| `x25519_identity` | `1502dacc6a53f7a3584406face44538b59636b480eb83d6dc542f4a7da3281f3` |
| `x25519_long_file_key` | `5196f3a6181304fbe3f34247f86a13b3bacd6c87d76e5e479c1d0aea211cdc52` |
| `x25519_long_share` | `06292c7ed03fb898b6211b3ac9179b74bffba86ff06cfffc9de026ea76e5650d` |
| `x25519_low_order` | `050745ff5f2d5981849852cb437e721579dd8fd84cefe7d687eb5b6addb4be07` |
| `x25519_lowercase` | `e5d37db1970674982e5e6fd526bf173378fe003082beaa363e152470d1a62853` |
| `x25519_multiple_recipients` | `4fa8ea5b02b3d8415f041a0f7f62c28b1d3b1ae4128b90ab42ef8ac114d2870a` |
| `x25519_no_match` | `106bf9a30497a59beda392a70e58b85d03c314ff305101dcbdafd66a77f3be77` |
| `x25519_not_canonical_body` | `b58c0df974726a612334934f3251c59de07bc437eb76ae310bfb439e4959eb18` |
| `x25519_not_canonical_share` | `d7c5157679bdaffc2b141c7efa56346225be19131f9f33c499c00f55ec532d1d` |
| `x25519_short_share` | `2ab19ad9c1554db0598acb4de1ef731b42cc77f2ab855cbc4191e5be50ae2759` |
