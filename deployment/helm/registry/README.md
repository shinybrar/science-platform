# registry

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 1.16.0](https://img.shields.io/badge/AppVersion-1.16.0-informational?style=flat-square)

A Helm chart to install the IVOA Registry

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| CADC |  | <https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/> |

## Requirements

Kubernetes: `>=1.23.0`

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| deployment.hostname | string | `"localhost"` | Hostname for the ingress resource used for deployment. In a producion environment this should be a FQDN. In development it can be a local hostname. |
| fullnameOverride | string | `""` |  |
| image.pullPolicy | string | `"IfNotPresent"` | Container image pull policy |
| image.repository | string | `"nginx"` | Container image repository |
| image.tag | string | `"latest"` | Container image tag, defaults to the Chart.AppVersion |
| imagePullSecrets | list | `[]` | Secrets for pulling a private image, [more info](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/) |
| nameOverride | string | `""` | Override the default chart name. |
| podAnnotations | object | `{}` | [Pod Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/) |
| podLabels | object | `{}` | [Pod Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/) |
| replicaCount | int | `1` | [ReplicaSet Count](https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/) |
| skaha.namespace | string | `"skaha-system"` | Namespace to deploy the application to, must exist before deployment. |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)