# TLS certificates for MAX Bot API (platform-api2.max.ru)
#
# MAX uses certificates issued by Минцифры (Russian Trusted Root/Sub CA).
# Standard Python/certifi trust stores do not include them, which causes:
#   SSLCertVerificationError: unable to get local issuer certificate
#
# Sources (official): https://gu-st.ru/content/Other/doc/
# - russian_trusted_root_ca.cer
# - russian_trusted_sub_ca.cer
#
# `ca_bundle.pem` is rebuilt automatically at runtime (certifi + these two files).
