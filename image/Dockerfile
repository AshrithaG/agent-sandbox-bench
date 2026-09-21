# The image an agent's code runs in. It is the same for every isolation level,
# so a difference in outcome comes from the runtime and its flags, not the image.
FROM alpine:3.20 AS build
RUN apk add --no-cache gcc musl-dev linux-headers
COPY probe.c /src/probe.c
RUN gcc -O2 -static -o /probe /src/probe.c

FROM python:3.12-alpine
RUN apk add --no-cache git curl && adduser -D -u 1000 agent
COPY --from=build /probe /usr/local/bin/probe
COPY workloads.py /usr/local/bin/workloads.py
USER agent
WORKDIR /home/agent
