# posixmapper

![Version: 0.1.10](https://img.shields.io/badge/Version-0.1.10-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.2.1](https://img.shields.io/badge/AppVersion-0.2.1-informational?style=flat-square)

A Helm chart to install the UID/GID POSIX Mapper

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| deployment.hostname | string | `"example.host.com"` | Hostname for the service, **Note:** Change this to your domain. |
| deployment.posixMapper.gmsID | string | `"ivo://example.org/gms"` | ID (URI) of the GMS Service. |
| deployment.posixMapper.image | string | `"images.opencadc.org/platform/posix-mapper:0.2.1"` | The image for the service |
| deployment.posixMapper.imagePullPolicy | string | `"Always"` | The image pull policy for the service |
| deployment.posixMapper.minGID | int | `900000` | Minimum GID, **should be high so as not to conflict with Image GIDs**. |
| deployment.posixMapper.minUID | int | `10000` | Minimum UID, **should be high so as not to conflict with Image UIDs**. |
| deployment.posixMapper.oidcURI | string | `"iam.example.org"` | URI or URL of the OIDC (IAM) server.  Used to validate incoming tokens. |
| deployment.posixMapper.registryURL | string | `"https://example.org/reg"` | Registry URL |
| deployment.posixMapper.resourceID | string | `"ivo://example.org/posix-mapper"` | IVOA Resource ID for the service |
| deployment.posixMapper.resources | object | `{"limits":{"cpu":"500m","memory":"1Gi"},"requests":{"cpu":"500m","memory":"1Gi"}}` | Resources provided to the Skaha service. |
| deployment.posixMapper.resources.limits.cpu | string | `"500m"` | CPU Limit |
| deployment.posixMapper.resources.limits.memory | string | `"1Gi"` | Memory Limit |
| deployment.posixMapper.resources.requests.cpu | string | `"500m"` | CPU Requested |
| deployment.posixMapper.resources.requests.memory | string | `"1Gi"` | Memory Requested |
| kubernetesClusterDomain | string | `"cluster.local"` |  |
| postgresql | object | `{"auth":{"database":"mapping","password":"posixmapperpwd","schema":"mapping","username":"posixmapper"},"maxActive":8,"storage":{"spec":{"hostPath":{"path":"/posix-mapper/data"}}}}` | The database connection information for the service |
| replicaCount | int | `1` | Replica count for the service, default is 1 |
| secrets | string | `nil` |  |
| skaha.namespace | string | `"skaha-system"` | The namespace for the service, **Note:** This namespace is common between the Skaha and POSIX Mapper services. Do not change. |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
