package com.driftshield.backend.dto.response;

import lombok.*;
@Data
@Builder
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
public class RuntimeConfigResponse {

    private Object ssh_port;

    private Object firewall_enabled;

    private Object public_access;

    private Object admin_endpoint_exposed;

    private Object max_requests_per_minute;
}