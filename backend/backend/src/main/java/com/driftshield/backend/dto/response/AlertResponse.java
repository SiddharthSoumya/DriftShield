package com.driftshield.backend.dto.response;

import lombok.Builder;
import lombok.Getter;

import java.time.LocalDateTime;

@Getter
@Builder
public class AlertResponse {

    private Long id;
    private String type;
    private String severity;
    private String message;
    private LocalDateTime createdAt;

}
