# syntax=docker/dockerfile:1

# --- Étape 1 : build Maven ---------------------------------------------------
FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -q -B dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -q -B package -DskipTests

# --- Étape 2 : runtime -------------------------------------------------------
FROM eclipse-temurin:21.0.8_9-jre

ARG OTEL_AGENT_VERSION=2.20.1
WORKDIR /app

# Agent OpenTelemetry Java téléchargé au build.
# Le dossier est créé avant : sinon ADD --chmod l'applique aussi au dossier (illisible).
RUN mkdir -p /otel
ADD --chmod=644 \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar \
    /otel/opentelemetry-javaagent.jar

COPY --from=build /src/target/app.jar /app/app.jar

RUN useradd --system --uid 10001 app
USER app

# L'agent est activé via JAVA_TOOL_OPTIONS ; la config OTEL_* est passée par le compose
ENV JAVA_TOOL_OPTIONS="-javaagent:/otel/opentelemetry-javaagent.jar"

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
