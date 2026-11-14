"""Allowlisted projections of provider responses. No credentials or raw responses leave the runner."""
import hashlib
import json
import re

from .contract import validate_values


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def put(result, identity, fields):
    if identity in result:
        raise ValueError("Duplicate resource identity.")
    result[identity] = fields


def terraform(plan):
    version = re.fullmatch(r"1\.([0-9]{1,3})", str(plan.get("format_version")))
    if not version or int(version[1]) < 1 or plan.get("errored"):
        raise ValueError("Unsupported Terraform/OpenTofu plan JSON.")
    if not isinstance(plan.get("planned_values"), dict) or not isinstance(plan.get("configuration"), dict):
        raise ValueError("Expected a plan with refreshed values and configuration.")
    if plan.get("complete") is False or plan.get("deferred_changes") or plan.get("action_invocations"):
        raise ValueError("Plan is incomplete or contains unsupported actions.")
    if any(row.get("change", {}).get("actions") != ["no-op"] for row in plan.get("resource_changes", [])):
        raise ValueError("Expected a refresh-only plan, without pending configuration changes.")
    result = {}
    for row in plan.get("resource_drift", []):
        resource_type = row["type"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", resource_type):
            raise ValueError("Invalid Terraform resource type.")
        actions = row["change"]["actions"]
        if actions == ["no-op"]:
            continue
        if not isinstance(actions, list) or not actions or not all(action in ("create", "update", "delete", "read", "forget") for action in actions):
            raise ValueError("Unsupported Terraform drift action.")
        identity = f"{resource_type}/{hashlib.sha256(row['address'].encode()).hexdigest()[:24]}"
        put(result, identity, {"action": "_".join(actions), "resource_type": resource_type})
    return result


def docker(containers):
    if not isinstance(containers, list):
        raise ValueError("Docker inspect must return a list.")
    result = {}
    for row in containers:
        host = row["HostConfig"]
        put(result, "container/" + row["Name"].lstrip("/"), {
            "image": row["Image"],
            "privileged": host["Privileged"],
            "read_only_root": host["ReadonlyRootfs"],
            "memory_bytes": host["Memory"],
            "nano_cpus": host["NanoCpus"],
            "network_mode": host["NetworkMode"] if host["NetworkMode"] in ("host", "none", "bridge", "default") else "custom",
            "restart_policy": host["RestartPolicy"]["Name"],
            "capabilities_digest": fingerprint(sorted(host.get("CapAdd") or [])),
            "port_bindings_digest": fingerprint({
                port: sorted([[binding["HostIp"], binding["HostPort"]] for binding in bindings or []])
                for port, bindings in (host.get("PortBindings") or {}).items()
            }),
        })
    return result


def kubernetes(documents, namespace):
    result = {}
    for document in documents:
        for row in document["items"]:
            kind = row["kind"]
            if kind not in ("Deployment", "StatefulSet", "DaemonSet", "Service", "HorizontalPodAutoscaler"):
                raise ValueError("Unexpected Kubernetes kind.")
            metadata, spec = row["metadata"], row["spec"]
            if metadata["namespace"] != namespace:
                raise ValueError("Kubernetes returned an unexpected namespace.")
            identity = f"{kind.lower()}/{namespace}/{metadata['name']}"
            if kind == "Service":
                fields = {
                    "service_type": spec.get("type", "ClusterIP"),
                    "ports_digest": fingerprint(sorted([
                        [port.get("name", ""), port.get("protocol", "TCP"), port["port"], str(port.get("targetPort", port["port"]))]
                        for port in spec.get("ports", [])
                    ])),
                }
            elif kind == "HorizontalPodAutoscaler":
                fields = {"min_size": spec.get("minReplicas", 1), "max_size": spec["maxReplicas"]}
            else:
                pod = spec["template"]["spec"]
                containers = pod["containers"] + pod.get("initContainers", [])
                fields = {
                    "images_digest": fingerprint(sorted((item["name"], item["image"]) for item in containers)),
                    "resources_digest": fingerprint(sorted((item["name"], item.get("resources", {})) for item in containers)),
                    "privileged": any(item.get("securityContext", {}).get("privileged") is True for item in containers),
                    "host_network": pod.get("hostNetwork", False),
                }
                if kind != "DaemonSet":
                    fields["replicas"] = spec.get("replicas", 1)
            put(result, identity, fields)
    return result


AWS_CALLS = {
    "ec2": ("ec2", "describe-instances"),
    "ebs": ("ec2", "describe-volumes"),
    "nat": ("ec2", "describe-nat-gateways"),
    "rds": ("rds", "describe-db-instances"),
    "asg": ("autoscaling", "describe-auto-scaling-groups"),
    "elb": ("elbv2", "describe-load-balancers"),
    "eip": ("ec2", "describe-addresses"),
}


def aws(documents):
    result = {}
    if set(documents) != set(AWS_CALLS):
        raise ValueError("AWS observation requires supported inventory responses.")
    for value in documents.values():
        if any(value.get(token) for token in ("NextToken", "Marker", "NextMarker", "nextToken")):
            raise ValueError("AWS inventory is incomplete.")
    for reservation in documents["ec2"]["Reservations"]:
        for row in reservation["Instances"]:
            if row["State"]["Name"] in ("terminated", "shutting-down"):
                continue
            put(result, "ec2/" + row["InstanceId"], {"instance_type": row["InstanceType"], "state": row["State"]["Name"]})
    for row in documents["ebs"]["Volumes"]:
        if row["State"] in ("deleted", "deleting"):
            continue
        put(result, "ebs/" + row["VolumeId"], {
            "size": row["Size"], "volume_type": row["VolumeType"], "encrypted": row["Encrypted"],
            "iops": row.get("Iops"), "throughput": row.get("Throughput"),
        })
    for row in documents["nat"]["NatGateways"]:
        if row["State"] in ("deleted", "deleting", "failed"):
            continue
        put(result, "nat/" + row["NatGatewayId"], {"connectivity_type": row["ConnectivityType"]})
    for row in documents["rds"]["DBInstances"]:
        if row["Engine"] in ("docdb", "neptune"):
            continue
        put(result, "rds/" + row["DBInstanceIdentifier"], {
            "instance_class": row["DBInstanceClass"], "engine": row["Engine"], "multi_az": row["MultiAZ"],
            "publicly_accessible": row["PubliclyAccessible"], "encrypted": row["StorageEncrypted"],
            "allocated_storage": row.get("AllocatedStorage"), "iops": row.get("Iops"),
        })
    for row in documents["asg"]["AutoScalingGroups"]:
        put(result, "asg/" + row["AutoScalingGroupName"], {
            "min_size": row["MinSize"], "max_size": row["MaxSize"], "desired_capacity": row["DesiredCapacity"],
        })
    for row in documents["elb"]["LoadBalancers"]:
        put(result, "elb/" + row["LoadBalancerArn"].rsplit("/", 1)[-1], {
            "load_balancer_type": row["Type"], "scheme": row["Scheme"],
            "availability_zones": len(row["AvailabilityZones"]),
        })
    for row in documents["eip"]["Addresses"]:
        put(result, "eip/" + row["AllocationId"], {"associated": bool(row.get("AssociationId"))})

    return result


def snapshot(document):
    return validate_values(document)
