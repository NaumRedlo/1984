FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
ARG TARGETARCH
WORKDIR /src
COPY src/Assay/ src/Assay/
RUN rid=linux-x64; [ "$TARGETARCH" = "arm64" ] && rid=linux-arm64; \
    dotnet publish src/Assay/Assay.csproj -c Release -r "$rid" --self-contained false -o /out \
    && dotnet /out/Assay.dll --self-test

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /out/ ./
ENV ASSAY_URLS=http://0.0.0.0:5077 \
    ASSAY_MAPS=/var/lib/assay/maps
RUN mkdir -p /var/lib/assay/maps && chown app /var/lib/assay/maps
VOLUME /var/lib/assay/maps
EXPOSE 5077
USER app
ENTRYPOINT ["dotnet", "Assay.dll"]
