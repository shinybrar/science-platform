# CARTA Kubernetes Deployment Guide

This directory contains Kubernetes manifests for deploying the CARTA authentication sidecar and controller components.

## Overview

The deployment consists of three main components:

1. **CARTA Controller** (`carta-controller.yaml`) - The main CARTA controller with an ephermal MongoDB backend
2. **CARTA Sidecar** (`carta-sidecar.yaml`) - ForwardAuth service for session authentication
3. **Traefik Middleware** (`traefik-middleware.yaml`) - Traefik middleware configuration for ForwardAuth

## Prerequisites

- Kubernetes cluster with RBAC enabled
- `kubectl` configured to access your cluster
- Traefik ingress controller installed (for middleware)
- Existing ServiceAccount named `skaha` in your system namespace

## Deployment Instructions

### 1. Deploy CARTA Controller and MongoDB

Deploy the controller and its MongoDB backend to your **system namespace**:

```bash
kubectl apply -f carta-controller.yaml -n <SYSTEM_NAMESPACE>
```

This creates:
- ConfigMap with CARTA controller configuration
- MongoDB deployment (ephemeral, for development only)
- MongoDB service (`carta-mongodb`)
- CARTA controller deployment
- CARTA controller service
- NetworkPolicy (optional, for default-deny environments)

### 2. Deploy CARTA Sidecar

Deploy the authentication sidecar to your **system namespace**:

```bash
kubectl apply -f carta-skaha-system-sidecar.yaml -n <SYSTEM_NAMESPACE>
```

This creates:
- RBAC Role for the sidecar
- RoleBinding linking the `skaha` ServiceAccount to the Role
- CARTA sidecar deployment
- CARTA sidecar service

### 3. Deploy Traefik Middleware

Deploy the ForwardAuth middleware to your **workload namespace**:

```bash
# First, update the middleware address if needed
sed -i 's/skaha-system/<SYSTEM_NAMESPACE>/g' traefik-middleware.yaml

# Then deploy to workload namespace
kubectl apply -f traefik-middleware.yaml -n <WORKLOAD_NAMESPACE>
```

This creates:
- Traefik Middleware resource for ForwardAuth

## Configuration

### Environment Variables

The CARTA sidecar supports these environment variables (configured in `carta-skaha-system-sidecar.yaml`):

- `TARGET_NAMESPACE`: Namespace to monitor for CARTA sessions (default: `skaha-workload`)
- `CACHE_TTL_SECONDS`: Cache TTL for user lookups (default: `3600`)
- `LOG_LEVEL`: Logging level (default: `DEBUG`)
- `DEV_MODE`: Enable development mode (default: `true`)
- `PROD`: Production mode flag (default: `false`)

### MongoDB Configuration

The MongoDB deployment is **ephemeral** and suitable for development only. For production:

1. Replace the MongoDB deployment with a persistent, secured instance
2. Update the `carta-controller-config` ConfigMap with the new MongoDB URI
3. Ensure proper authentication and TLS configuration

### Namespace Considerations

- **System Namespace**: Deploy `carta-controller.yaml` and `carta-skaha-system-sidecar.yaml`
- **Workload Namespace**: Deploy `traefik-middleware.yaml`
- Update the middleware address in `traefik-middleware.yaml` to reference the correct system namespace

## Verification

### Check Deployments

```bash
# Check system namespace deployments
kubectl get deployments -n <SYSTEM_NAMESPACE>

# Check workload namespace middleware
kubectl get middleware -n <WORKLOAD_NAMESPACE>
```

### Check Services

```bash
# Verify services are running
kubectl get services -n <SYSTEM_NAMESPACE>
```

### Check Logs

```bash
# CARTA Controller logs
kubectl logs -n <SYSTEM_NAMESPACE> deployment/carta-controller

# CARTA Sidecar logs
kubectl logs -n <SYSTEM_NAMESPACE> deployment/carta-sidecar

# MongoDB logs
kubectl logs -n <SYSTEM_NAMESPACE> deployment/carta-mongodb
```

## Troubleshooting

### Common Issues

1. **ServiceAccount not found**: Ensure the `skaha` ServiceAccount exists in your system namespace
2. **Middleware not working**: Verify the ForwardAuth address points to the correct namespace
3. **MongoDB connection issues**: Check that the `carta-mongodb` service is accessible from the controller
4. **RBAC issues**: Ensure the sidecar has proper permissions to list pods and services

### Health Checks

The sidecar provides health check endpoints:
- Liveness: `http://carta-sidecar/livez`
- Readiness: `http://carta-sidecar/readyz`

## Security Notes

- The current MongoDB deployment has **no authentication** and is ephemeral
- For production, implement proper MongoDB security
- Review and adjust RBAC permissions as needed
- Consider network policies for additional security

## Development

For development and testing, you can use the intercept tool:

```bash
# From the project root
uv run carta-intercept --help
```

This tool helps with session interception and debugging during development.
