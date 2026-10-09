# Deploy walkthrough

Step-by-step for a first deploy of the single-node Elasticsearch + Kibana
stack on Akamai Cloud, locked to the DataStream 2 IP ACL ranges.

Prereqs on your machine: Terraform >= 1.6, `linode-cli`, `gh`, and SSH.

## 1. Get a Linode Personal Access Token

1. Log in to https://cloud.linode.com/profile/tokens
2. Click **Create a Personal Access Token**.
3. Scopes: give Read/Write on at least:
   - Linodes
   - Firewalls
   - VPCs
   - Volumes
   - StackScripts (Read)
4. Copy the token once (you cannot see it again) and export it:
   ```bash
   export LINODE_TOKEN=xxxxxxxxxxxxxxxx
   ```
5. (Optional) stash it in your shell rc:
   ```bash
   echo 'export LINODE_TOKEN=xxxxxxxxxxxxxxxx' >> ~/.zshrc
   ```

## 2. Review and apply the Terraform

```bash
cd ~/Desktop/jcavaiuolo.net.ar/datastream2-elk-blog/terraform

# The lock file is already committed. Just init to download providers.
terraform init

# Dry run. Expect ~5 resources: firewall, vpc, subnet, instance, volume.
terraform plan -out tfplan

# Build.
terraform apply tfplan
```

Terraform waits for the instance to come up (~1 min), then returns. You will
see outputs like:

```
elasticsearch_bulk_endpoint = "http://<rdns>:9200/_bulk"
kibana_url                  = "http://<ip>:5601/"
public_ipv4                 = "x.x.x.x"
reverse_dns_hint            = "<ip-dashed>.ip.linodeusercontent.com"
```

Save the Reverse DNS value, DataStream 2 needs it.

## 3. Wait for the StackScript to finish

The compute instance is up but the install runs for ~10 minutes. Monitor:

```bash
ssh -i ~/.ssh/ds2-elk-tf jcavaiuolo@$(terraform -chdir=. output -raw public_ipv4) \
    'sudo tail -f /var/log/stackscript.log'
```

Done when you see the final lines matching the Elasticsearch service "started"
and the dashboard import success. Press Ctrl+C to leave the tail.

## 4. Sanity check Elasticsearch and Kibana

```bash
# From your machine
curl -u elastic:<es_admin_password> "http://$(terraform output -raw public_ipv4):9200/_cluster/health?pretty"
```

You should see `"status": "green"` or `"yellow"` (yellow is fine on a one-node
cluster, replicas are not placed).

Open Kibana: paste `terraform output -raw kibana_url` in your browser. Log in
as `elastic` with the Elasticsearch password you set in `terraform.tfvars`.

## 5. Configure DataStream 2

Follow the "Configure DataStream 2" section in `post.md`:

1. Akamai Control Center -> COMMON SERVICES -> DataStream -> Create a stream.
2. Log type: `CDN`.
3. Pick the delivery properties.
4. Fields: Include all for a first install, trim later.
5. Format: JSON.
6. Destination:
   - Type: Elasticsearch
   - Endpoint: `http://<reverse_dns_hint>:9200/_bulk`
   - Index name: `datastream2-cdn`
   - Username: `ds2_ingest`
   - Password: the ds2_ingest_password from `terraform.tfvars`
   - Send compressed data: on
7. Validate & Save.
8. Activate the stream. Takes ~90 minutes.

## 6. Enable DataStream in Property Manager

In the property fronting your traffic:

- Add the `DataStream` and `Log Request Details` behaviors to the default rule.
- Stream version: v2.
- Stream names: the one you just created.
- Sampling rate: 100 for the demo.
- Log Akamai Edge Server IP Address: on (required).
- Save and activate on staging first.

## 7. Verify

Hit the property a few times from your laptop (`curl -v https://<hostname>/`),
wait 1-2 minutes, then in Kibana go to Discover and query the
`datastream2-cdn-*` data view. You should see your requests.

If the "Akamai" dashboard was pre-loaded by the StackScript, open it and
confirm the panels start filling in.

## Troubleshooting

- **Terraform error "unauthorized"**: `LINODE_TOKEN` not exported, or expired.
- **StackScript stuck**: `ssh ... 'systemctl status elasticsearch kibana'`. If
  `elasticsearch.service` is dead, check `/var/log/elasticsearch/*.log`. A
  common cause is too little RAM, bump the instance type.
- **DS2 "Destination details are valid" fails**: firewall rule not accepting
  the DS2 IPs. Confirm the IP ACL list in `terraform.tfvars` matches the
  current list in the Akamai TechDocs changelog.
- **Kibana login loops**: cookies, or wrong password. Reset with
  `sudo /usr/share/elasticsearch/bin/elasticsearch-reset-password -u elastic`
  on the box, then update `terraform.tfvars` and re-apply (no-op on infra).
- **No data in Kibana after 2 hours**: the stream can take up to 90 minutes to
  activate end to end. Confirm the stream status is "Activated", confirm the
  property is activated, and confirm `curl` requests are hitting the Akamai
  edge, not origin.

## Teardown when done

```bash
terraform destroy
```

Removes the instance, firewall, VPC, subnet, and volume. The DataStream 2
stream and property changes are not managed by Terraform, delete them from
the Control Center manually.
