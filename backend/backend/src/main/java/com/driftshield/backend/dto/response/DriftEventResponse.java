package com.driftshield.backend.dto.response;

import lombok.Builder;
import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;
@Builder
@Getter
@Setter
public class DriftEventResponse {
    private Long id;
    private String configKey;
    private String oldValue;
    private String newValue;
    private String severity;
    private boolean reverted;
    private LocalDateTime createdAt;
}
